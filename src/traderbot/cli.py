"""traderbot: `propose SYMBOL`, `approve PREFIX` (argv in Phase 1 only), `status`.

`propose` reads bars, asks the desk, sizes the order in code, runs the gate and stores a card.
`approve` recomputes the card's digest, returns the order already placed under that client id
if there is one, and otherwise reruns the gate live and places a DAY limit buy on paper.
"""

import argparse
import re
import sqlite3
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta

from alpaca.trading.client import TradingClient
from alpaca.trading.models import Order

from traderbot import broker, data, desk, gate, keychain, ledger
from traderbot import proposal as prop

SYMBOL_RE = re.compile(r"[A-Z]{1,5}(?:\.[A-Z]{1,2})?")
LIMITS = gate.Limits()


class Refused(Exception):
    """A command that stops on purpose (spend cap, tamper, gate deny). Exit code 1."""


def _symbol(raw: str) -> str:
    symbol = raw.strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise Refused(f"not a US stock or ETF symbol: {shown(raw)}")
    return symbol


def shown(text: str) -> str:
    """Model or user text for the terminal: control characters escaped, never interpreted."""
    return "".join(ch if ch.isprintable() else ch.encode("unicode_escape").decode() for ch in text)


def _snapshot(
    trading: TradingClient,
    conn: sqlite3.Connection,
    now: datetime,
    expected_as_of: date,
    exclude: str | None,
) -> gate.AccountSnapshot:
    return gate.AccountSnapshot(
        buying_power=broker.buying_power(broker.account(trading)),
        open_order_symbols=broker.open_order_symbols(trading),
        expected_as_of=expected_as_of,
        day_spend_usd=ledger.day_spend(conn, now.date(), exclude),
    )


def propose(
    raw_symbol: str,
    limits: gate.Limits = LIMITS,
    decide: Callable[[data.Bars], desk.DeskResult] = desk.decide,
) -> ledger.Card:
    """`decide` is the desk; the live approve test passes a code built buy so no model runs."""
    symbol = _symbol(raw_symbol)
    now = data.now_et()
    conn = ledger.connect()
    spent = ledger.day_spend(conn, now.date())
    if spent >= limits.day_spend_cap_usd:
        raise Refused(f"day model spend ${spent} reached the ${limits.day_spend_cap_usd} cap")
    keys = keychain.load()
    trading = broker.client(keys)
    bars = data.daily_bars(trading, keys, symbol, now)
    if bars.stale:
        raise Refused(
            f"bars are stale: latest complete {symbol} bar is {bars.bar_date}, last closed session"
            f" {bars.as_of}; no desk call made"
        )
    result = decide(bars)
    limit, qty = prop.size(bars.last_close, limits.max_notional, limits.band)
    choice = result.choice
    card = prop.Proposal(
        symbol=choice.symbol if choice else symbol,
        requested_symbol=symbol,
        side=choice.side if choice else "review",
        rationale=choice.rationale if choice else str(result.review_reason),
        confidence=choice.confidence if choice else None,
        qty=qty,
        limit_price=limit,
        order_type="limit",
        time_in_force="day",
        as_of=bars.as_of,
        bar_date=bars.bar_date,
        last_close=bars.last_close,
        desk="single",
        model=desk.MODEL,
        created_at=now,
    )
    snapshot = _snapshot(trading, conn, now, bars.as_of, None)
    verdict = gate.check(card, card.requested_symbol, snapshot, limits, now)
    key = ledger.insert_card(conn, card, result.cost_usd, result.model_usage, verdict)
    return ledger.find(conn, key)


def approve(prefix: str, limits: gate.Limits = LIMITS) -> Order:
    conn = ledger.connect()
    try:
        card = ledger.find(conn, prefix)
    except (LookupError, ValueError) as err:
        raise Refused(str(err)) from err
    if prop.digest_of(card.proposal_json) != card.digest:
        raise Refused(f"card {card.digest[:8]} was altered after it was stored; refusing")
    if not card.allowed:
        raise Refused(f"card {card.digest[:8]} was denied at propose: {', '.join(card.reasons)}")
    trading = broker.client(keychain.load())
    now = data.now_et()
    existing = broker.order_by_client_id(trading, card.digest)
    if existing is not None:
        ledger.record_order(conn, card.digest, str(existing.id), existing.status.value, now)
        return existing
    window = data.sessions(trading, now.date() - timedelta(days=10), now.date())
    expected = data.last_closed(window, now).day
    snapshot = _snapshot(trading, conn, now, expected, card.digest)
    verdict = gate.check(card.proposal, card.proposal.requested_symbol, snapshot, limits, now)
    if isinstance(verdict, gate.Deny):
        raise Refused(f"gate denies card {card.digest[:8]} now: {', '.join(verdict.reasons)}")
    placed = broker.submit_limit_buy(trading, card.proposal, card.digest)
    ledger.record_order(conn, card.digest, str(placed.id), placed.status.value, now)
    return placed


def _line(card: ledger.Card) -> str:
    p = card.proposal
    verdict = "ALLOW" if card.allowed else "DENY " + ",".join(card.reasons)
    order = f"order {card.order_id} {card.order_status}" if card.order_id else "no order"
    return (
        f"{card.digest[:8]}  {p.created_at:%Y-%m-%d %H:%M}  {shown(p.symbol)} {p.side} "
        f"{p.qty} @ {p.limit_price}  {verdict}  ${card.total_cost_usd:.4f}  {order}"
    )


def _print_propose(card: ledger.Card) -> None:
    p = card.proposal
    print(f"card {_line(card)}  as_of {p.as_of}")
    print(f"rationale: {shown(p.rationale)}")
    if card.allowed:
        print(f"approve in Terminal.app: uv run traderbot approve {card.digest[:8]}")
    print(f"ledger: {ledger.path()}")


def _print_status() -> None:
    conn = ledger.connect()
    cards = ledger.recent(conn)
    today = data.now_et().date()
    spent = ledger.day_spend(conn, today)
    print(
        f"traderbot status  cards {len(cards)}  spend {today} ${spent:.4f} of ${LIMITS.day_spend_cap_usd}"
    )
    for card in cards:
        print(_line(card))
    print(f"ledger: {ledger.path()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traderbot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("propose", help="ask the desk about one symbol and store a card").add_argument(
        "symbol"
    )
    sub.add_parser("approve", help="place the order for a stored card").add_argument("prefix")
    sub.add_parser("status", help="cards, orders and the day's model spend")
    args = parser.parse_args(argv)
    try:
        if args.command == "propose":
            _print_propose(propose(args.symbol))
        elif args.command == "approve":
            order = approve(args.prefix)
            print(
                f"order {order.id}  status {order.status.value}  client id {order.client_order_id}"
            )
        else:
            _print_status()
    except Refused as err:
        print(f"refused: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
