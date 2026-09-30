"""traderbot: propose, approve (typed at the terminal), reject, status, off, on, reset-breaker.

`propose` reads bars and refuses early (kill switch, stale bars, tripped breaker, spend cap)
before any desk call; otherwise it reserves the call cap, asks the desk, settles the cost, sizes
the order in code, runs the gate and stores a card. `approve` reads an 8 character prefix from
/dev/tty, checks the card, returns an order already placed under its digest or reruns the gate
live, records the first decision and submits. Every command takes --json.
"""

import argparse
import errno
import hmac
import io
import json
import re
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, TextIO

from alpaca.trading.client import TradingClient
from alpaca.trading.models import Order

from traderbot import broker, data, desk, gate, keychain, ledger
from traderbot import proposal as prop

SYMBOL_RE = re.compile(r"[A-Z]{1,5}(?:\.[A-Z]{1,2})?")
PREFIX_LEN = 8
TYPED_PREFIX_RE = re.compile(r"[0-9a-f]{8}")
TTY = "/dev/tty"
LIMITS = gate.Limits()
# Hangul fillers render as blank space but are letters, so isprintable() passes them.
FILLERS = frozenset({"\u115f", "\u1160", "\u3164", "\uffa0"})


class Refused(Exception):
    """A command that stops on purpose (spend cap, stale bars, tamper, gate deny). Exit code 1."""


def shown(text: str) -> str:
    """Model or user text for the terminal: line breaks, control, format and invisible characters
    print as escapes, so text can neither drive the terminal nor forge a line of output."""
    return "".join(
        ch if ch.isprintable() and ch not in FILLERS else ch.encode("unicode_escape").decode()
        for ch in text
    )


def _symbol(raw: str) -> str:
    symbol = raw.strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise Refused(f"not a US stock or ETF symbol: {shown(raw)}")
    return symbol


def _snapshot(
    trading: TradingClient,
    conn: sqlite3.Connection,
    now: datetime,
    symbol: str,
    expected_as_of: date,
) -> gate.AccountSnapshot:
    acct = broker.account(trading)
    equity = broker.equity(acct)
    qty, value = broker.position(trading, symbol)
    return gate.AccountSnapshot(
        buying_power=broker.buying_power(acct),
        equity=equity,
        equity_peak=ledger.mark_equity(conn, equity, now),
        position_qty=qty,
        position_value=value,
        open_order_symbols=broker.open_order_symbols(trading),
        expected_as_of=expected_as_of,
        day_spend_usd=ledger.day_spend(conn, now.date(), now),
        orders_today=ledger.orders_today(conn, now.date()),
        consecutive_losses=ledger.consecutive_losses(conn),
        kill_switch=ledger.kill_switch_on(),
        breaker_latched=ledger.breaker_latched(conn),
    )


def _tripped(
    conn: sqlite3.Connection, snapshot: gate.AccountSnapshot, limits: gate.Limits, now: datetime
) -> bool:
    """Latch the breaker the first time its condition holds; True while it is latched."""
    if snapshot.breaker_latched:
        return True
    if not gate.breaker_condition(snapshot, limits):
        return False
    reason = (
        f"{snapshot.consecutive_losses} settled losses in a row; equity {snapshot.equity}"
        f" against a peak of {snapshot.equity_peak}"
    )
    ledger.latch_breaker(conn, reason, now)
    ledger.log_event(conn, "breaker", now, details={"reason": reason})
    return True


def _find(conn: sqlite3.Connection, prefix: str) -> ledger.Card:
    try:
        return ledger.find(conn, prefix)
    except (LookupError, ValueError) as err:
        raise Refused(str(err)) from err


def propose(
    raw_symbol: str,
    limits: gate.Limits = LIMITS,
    decide: Callable[[data.Bars, int], desk.DeskResult] = desk.decide,
    desk_label: str = "single",
) -> ledger.Card:
    """`decide` is the desk. A code built buy (the live order test, the Phase 2 positive
    control) passes its own `decide` and a `desk_label` other than "single", which the card
    carries under its digest so scoring can leave it out."""
    symbol = _symbol(raw_symbol)
    now = data.now_et()
    conn = ledger.connect()
    if ledger.kill_switch_on():
        raise Refused("the kill switch is on (`traderbot on` clears it); no desk call made")
    keys = keychain.load()
    trading = broker.client(keys)
    bars = data.daily_bars(trading, keys, symbol, now)
    if bars.stale:
        raise Refused(
            f"bars are stale: latest complete {symbol} bar is {bars.bar_date}, last closed session"
            f" {bars.as_of}; no desk call made"
        )
    snapshot = _snapshot(trading, conn, now, symbol, bars.as_of)
    if _tripped(conn, snapshot, limits, now):
        raise Refused("the circuit breaker is tripped (`traderbot reset-breaker`); no desk call")
    window = data.sessions(trading, now.date(), now.date() + timedelta(days=10))
    expires_at = data.next_close(window, now)
    try:
        rid = ledger.reserve(conn, desk.CALL_CAP_USD, limits.day_spend_cap_usd, now)
    except ledger.SpendCapReached as err:
        raise Refused(f"{err}; no desk call made") from err
    result = decide(bars, snapshot.position_qty)
    ledger.settle(conn, rid, result.cost_usd, now)
    choice = result.choice
    side = choice.side if choice else "review"
    limit, qty = prop.size(
        bars.last_close, limits.max_notional, limits.band, side, snapshot.position_qty
    )
    card = prop.Proposal(
        symbol=choice.symbol if choice else symbol,
        requested_symbol=symbol,
        side=side,
        rationale=choice.rationale if choice else str(result.review_reason),
        confidence=choice.confidence if choice else None,
        qty=qty,
        limit_price=limit,
        order_type="limit",
        time_in_force="day",
        as_of=bars.as_of,
        bar_date=bars.bar_date,
        last_close=bars.last_close,
        desk=desk_label,
        model=desk.MODEL,
        created_at=now,
        expires_at=expires_at,
    )
    settled = replace(snapshot, day_spend_usd=ledger.day_spend(conn, now.date(), now))
    verdict = gate.check(card, card.requested_symbol, settled, limits, now)
    key = ledger.insert_card(conn, card, result.cost_usd, result.model_usage, verdict)
    stored = ledger.find(conn, key)
    ledger.log_event(conn, "propose", now, key, card_view(stored))
    return stored


def approve(prefix: str, limits: gate.Limits = LIMITS) -> Order:
    """The in process approve: the CLI reaches it only through `approve_at_terminal`."""
    conn = ledger.connect()
    card = _find(conn, prefix)
    short = card.digest[:PREFIX_LEN]
    if prop.digest_of(card.proposal_json) != card.digest:
        raise Refused(f"card {short} was altered after it was stored; refusing")
    if not card.allowed:
        raise Refused(f"card {short} was denied at propose: {', '.join(card.reasons)}")
    if card.decision == "reject":
        raise Refused(f"card {short} was rejected")
    trading = broker.client(keychain.load())
    now = data.now_et()
    existing = broker.order_by_client_id(trading, card.digest)
    if existing is not None:
        _decide(conn, card.digest, now)
        ledger.record_order(conn, card.digest, str(existing.id), existing.status.value, now)
        ledger.log_event(conn, "order", now, card.digest, {"order_id": str(existing.id)})
        return existing
    window = data.sessions(trading, now.date() - timedelta(days=10), now.date())
    expected = data.last_closed(window, now).day
    snapshot = _snapshot(trading, conn, now, card.proposal.symbol, expected)
    _tripped(conn, snapshot, limits, now)
    verdict = gate.check(card.proposal, card.proposal.requested_symbol, snapshot, limits, now)
    if isinstance(verdict, gate.Deny):
        ledger.log_event(conn, "refused", now, card.digest, {"reasons": list(verdict.reasons)})
        raise Refused(f"gate denies card {short} now: {', '.join(verdict.reasons)}")
    _decide(conn, card.digest, now)
    placed = broker.submit_limit_order(trading, card.proposal, card.digest)
    ledger.record_order(conn, card.digest, str(placed.id), placed.status.value, now)
    ledger.log_event(conn, "order", now, card.digest, {"order_id": str(placed.id)})
    return placed


def _decide(conn: sqlite3.Connection, key: str, now: datetime) -> None:
    standing = ledger.record_decision(conn, key, "approve", now)
    if standing != "approve":
        raise Refused(f"card {key[:PREFIX_LEN]} was rejected")
    ledger.log_event(conn, "decision", now, key, {"decision": "approve"})


def reject(prefix: str) -> str:
    """Record a no for the card; the first decision stands."""
    conn = ledger.connect()
    card = _find(conn, prefix)
    now = data.now_et()
    standing = ledger.record_decision(conn, card.digest, "reject", now)
    if standing != "reject":
        raise Refused(f"card {card.digest[:PREFIX_LEN]} already decided: {standing}")
    ledger.log_event(conn, "decision", now, card.digest, {"decision": "reject"})
    return standing


def open_tty() -> TextIO:
    try:
        raw = io.FileIO(TTY, "r+")  # unbuffered: a terminal is not seekable, so no BufferedRandom
    except OSError as err:
        if err.errno != errno.ENXIO:
            raise
        raise Refused(f"approval is typed at {TTY} and this process has none ({err})") from err
    return io.TextIOWrapper(raw, encoding="utf-8", write_through=True)


def read_prefix(tty_in: TextIO, tty_out: TextIO) -> str:
    """Read exactly 8 lowercase hex characters typed at the terminal."""
    tty_out.write(f"type the {PREFIX_LEN} character prefix of the card to approve: ")
    tty_out.flush()
    typed = tty_in.readline().strip()
    if not TYPED_PREFIX_RE.fullmatch(typed):
        raise Refused(f"a card prefix is exactly {PREFIX_LEN} lowercase hex characters")
    return typed


def approve_at_terminal(limits: gate.Limits = LIMITS) -> Order:
    """List the cards waiting for a decision on /dev/tty and approve the one typed there."""
    with open_tty() as tty:
        conn = ledger.connect()
        now = data.now_et()
        waiting = [
            card
            for card in ledger.recent(conn, 50)
            if card.allowed
            and card.decision is None
            and card.order_id is None
            and card.proposal.expires_at is not None
            and now < card.proposal.expires_at
        ]
        if not waiting:
            raise Refused("no card is waiting for a decision")
        for card in waiting:
            tty.write(_line(card) + "\n")
        typed = read_prefix(tty, tty)
        if not any(hmac.compare_digest(c.digest[:PREFIX_LEN], typed) for c in waiting):
            raise Refused("the typed prefix matches no waiting card")
        return approve(typed, limits)


def reset_breaker_at_terminal() -> int:
    """Clear a tripped breaker after `reset` is typed at /dev/tty; returns trips cleared."""
    with open_tty() as tty:
        conn = ledger.connect()
        if not ledger.breaker_latched(conn):
            return 0
        tty.write("the circuit breaker is tripped; type reset to clear it: ")
        tty.flush()
        if tty.readline().strip() != "reset":
            raise Refused("the breaker stays tripped")
        now = data.now_et()
        count = ledger.reset_breaker(conn, now)
        ledger.log_event(conn, "breaker", now, details={"reset": count})
        return count


def status() -> dict[str, Any]:
    """Refresh every open order from Alpaca (only then are keys read), then the ledger view."""
    conn = ledger.connect()
    now = data.now_et()
    pending = ledger.open_orders(conn)
    if pending:
        trading = broker.client(keychain.load())
        for key in pending:
            order = broker.order_by_client_id(trading, key)
            if order is not None:
                ledger.record_order(
                    conn,
                    key,
                    str(order.id),
                    order.status.value,
                    now,
                    None if order.filled_qty is None else str(order.filled_qty),
                    None if order.filled_avg_price is None else str(order.filled_avg_price),
                )
    return {
        "day": now.date().isoformat(),
        "spend_usd": str(ledger.day_spend(conn, now.date(), now)),
        "cap_usd": str(LIMITS.day_spend_cap_usd),
        "kill_switch": ledger.kill_switch_on(),
        "breaker_latched": ledger.breaker_latched(conn),
        "cards": [card_view(card) for card in ledger.recent(conn)],
        "ledger": str(ledger.path()),
    }


def card_view(card: ledger.Card) -> dict[str, Any]:
    """A card as plain JSON values; model text stays raw here and is escaped by json.dumps."""
    p = card.proposal
    return {
        "digest": card.digest,
        "created_at": p.created_at.isoformat(),
        "expires_at": None if p.expires_at is None else p.expires_at.isoformat(),
        "as_of": p.as_of.isoformat(),
        "symbol": p.symbol,
        "requested_symbol": p.requested_symbol,
        "side": p.side,
        "desk": p.desk,
        "qty": p.qty,
        "limit_price": str(p.limit_price),
        "rationale": p.rationale,
        "confidence": p.confidence,
        "allowed": card.allowed,
        "reasons": list(card.reasons),
        "cost_usd": str(card.total_cost_usd),
        "decision": card.decision,
        "order_id": card.order_id,
        "order_status": card.order_status,
        "filled_qty": card.filled_qty,
    }


def _line(card: ledger.Card) -> str:
    p = card.proposal
    verdict = "ALLOW" if card.allowed else "DENY " + ",".join(card.reasons)
    order = f"order {card.order_id} {card.order_status}" if card.order_id else "no order"
    decided = f"  {card.decision}" if card.decision else ""
    return (
        f"{card.digest[:PREFIX_LEN]}  {p.created_at:%Y-%m-%d %H:%M}  {shown(p.symbol)} {p.side} "
        f"{p.qty} @ {p.limit_price}  {verdict}  ${card.total_cost_usd:.4f}  {order}{decided}"
    )


def _readable(command: str, view: dict[str, Any]) -> list[str]:
    """The default view: one header line of key facts, the content, then where the ledger is."""
    if command == "propose":
        card = view["card"]
        lines = [f"card {view['line']}  as_of {card['as_of']}"]
        lines.append(f"rationale: {shown(card['rationale'])}")
        if card["allowed"]:
            lines.append(
                f"approve in Terminal.app: uv run traderbot approve, then type {view['prefix']}"
            )
        return [*lines, f"ledger: {ledger.path()}"]
    if command == "approve":
        return [
            (
                f"order {view['order_id']}  status {view['status']}"
                f"  client id {view['client_order_id']}"
            )
        ]
    if command == "reject":
        return [f"card {view['digest'][:PREFIX_LEN]} rejected"]
    if command in ("off", "on"):
        return [f"kill switch {'on: nothing is submitted' if view['kill_switch'] else 'off'}"]
    if command == "reset-breaker":
        return [f"circuit breaker trips cleared: {view['reset']}"]
    header = (
        f"traderbot status  cards {len(view['cards'])}  spend {view['day']}"
        f" ${float(view['spend_usd']):.4f} of ${view['cap_usd']}"
        f"  kill switch {'ON' if view['kill_switch'] else 'off'}"
        f"  breaker {'TRIPPED' if view['breaker_latched'] else 'ok'}"
    )
    lines = [_line(card) for card in ledger.recent(ledger.connect())]
    return [header, *lines, f"ledger: {view['ledger']}"]


def _run(command: str, args: argparse.Namespace) -> dict[str, Any]:
    if command == "propose":
        card = propose(args.symbol)
        return {"card": card_view(card), "line": _line(card), "prefix": card.digest[:PREFIX_LEN]}
    if command == "approve":
        order = approve_at_terminal()
        return {
            "order_id": str(order.id),
            "status": order.status.value,
            "client_order_id": order.client_order_id,
        }
    if command == "reject":
        reject(args.prefix)
        return {"digest": _find(ledger.connect(), args.prefix).digest, "decision": "reject"}
    if command in ("off", "on"):
        on = command == "off"
        ledger.set_kill_switch(on)
        ledger.log_event(ledger.connect(), "kill_switch", data.now_et(), details={"on": on})
        return {"kill_switch": ledger.kill_switch_on()}
    if command == "reset-breaker":
        return {"reset": reset_breaker_at_terminal()}
    return status()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traderbot")
    sub = parser.add_subparsers(dest="command", required=True)
    commands = {
        "propose": "ask the desk about one symbol and store a card",
        "approve": "type a card prefix at the terminal to place its order",
        "reject": "record a no for a card",
        "status": "cards, orders, spend, kill switch and breaker",
        "off": "turn the kill switch on: nothing is submitted",
        "on": "turn the kill switch off",
        "reset-breaker": "clear a tripped circuit breaker, typed at the terminal",
    }
    parsers = {name: sub.add_parser(name, help=text) for name, text in commands.items()}
    parsers["propose"].add_argument("symbol")
    parsers["reject"].add_argument("prefix")
    for each in parsers.values():
        each.add_argument("--json", action="store_true", help="machine JSON on stdout")
    args = parser.parse_args(argv)
    try:
        view = _run(args.command, args)
    except Refused as err:
        print(f"refused: {shown(str(err))}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(view, ensure_ascii=True, sort_keys=True))
    else:
        print("\n".join(_readable(args.command, view)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
