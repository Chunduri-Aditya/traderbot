"""Attack lens: clock, card expiry, the market calendar and the circuit breaker (Phase 2).

Offline only. conftest.py makes claude, the Keychain and HTTP tripwires and points
TRADERBOT_HOME at tmp_path; every Alpaca call below is a stub.
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from support import NEXT_CLOSE, NOW, SESSIONS, a_card

from traderbot import broker, cli, data, gate, keychain, ledger


def _stub_account(monkeypatch: pytest.MonkeyPatch, equity: Decimal, tick: Any = None) -> None:
    """Alpaca stand ins: all cash, no position, no open orders. `tick` runs on each network call."""

    def net(value: Any) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            if tick is not None:
                tick()
            return value

        return call

    monkeypatch.setattr(keychain, "load", lambda: keychain.AlpacaKeys("key", "secret"))
    monkeypatch.setattr(broker, "client", lambda keys: object())
    monkeypatch.setattr(broker, "order_by_client_id", net(None))
    monkeypatch.setattr(data, "sessions", net(SESSIONS))
    monkeypatch.setattr(broker, "account", net(object()))
    monkeypatch.setattr(broker, "position", net((0, Decimal(0))))
    monkeypatch.setattr(broker, "open_order_symbols", net(frozenset()))
    monkeypatch.setattr(broker, "buying_power", lambda acct: Decimal(100000))
    monkeypatch.setattr(broker, "equity", lambda acct: equity)


def _order(key: str) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(), status=SimpleNamespace(value="accepted"), client_order_id=key
    )


def _approve(prefix: str) -> Any:
    """The order approve returns, or the refusal text, so a refusal reads as an assertion."""
    try:
        return cli.approve(prefix)
    except cli.Refused as err:
        return f"refused: {err}"


def test_approve_submits_after_the_card_expired_when_the_clock_is_read_before_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec, Phase 2 Approval: "A card expires at the next session close."
    Spec, Phase 1 step 6: "Rerun the gate against the live clock, buying power and open orders."
    ARCHITECTURE.md Flow: "else `gate` rerun on live account state against the signed
    `requested_symbol`, the first decision recorded (sqlite primary key), then a DAY limit order
    on the paper host unless the kill switch file exists."

    Expected (from the spec line): no order is submitted at or after the card's expiry,
    NEXT_CLOSE (16:00 ET on 2026-09-30), so every submit instant is < NEXT_CLOSE, or approve
    refuses with CARD_EXPIRED.

    `approve` reads `now` once, then makes five Alpaca round trips (order lookup, calendar,
    account, position, open orders) before `submit_limit_order`, and checks CARD_EXPIRED
    against that early `now`. Each stubbed round trip here costs 0.3 s of simulated latency and
    approve starts 1 s before the close, so the submit lands at 16:00:00.5, after the card
    expired; Alpaca queues a DAY order sent after the close for the next session. The kill
    switch, by contrast, is rechecked inside `submit_limit_order`. Tension to resolve in the
    main loop: RULES.md "Every "now" is `datetime.now(ZoneInfo("America/New_York"))`, taken once
    per command and passed down." already reads two clocks in the `approve` command
    (`approve_at_terminal` and `approve`), and a fix that passes one `now` down makes this
    window wider, not narrower.
    """
    clock = SimpleNamespace(now=NEXT_CLOSE - timedelta(seconds=1))

    def tick() -> None:
        clock.now = clock.now + timedelta(milliseconds=300)

    _stub_account(monkeypatch, Decimal(100000), tick)
    monkeypatch.setattr(data, "now_et", lambda: clock.now)
    submitted_at: list[datetime] = []

    def submit(trading: object, card: Any, client_order_id: str) -> Any:
        submitted_at.append(clock.now)
        return _order(client_order_id)

    monkeypatch.setattr(broker, "submit_limit_order", submit)
    conn = ledger.connect()
    key = ledger.insert_card(conn, a_card(), Decimal("0.01"), ("claude-sonnet-5-5",), gate.Allow())
    outcome = _approve(key[:8])
    if isinstance(outcome, str):
        assert "CARD_EXPIRED" in outcome, outcome
    late = [at.isoformat() for at in submitted_at if at >= NEXT_CLOSE]
    assert late == [], f"order submitted after the card expired at {NEXT_CLOSE.isoformat()}"


def test_reset_breaker_cannot_release_a_drawdown_trip_on_an_all_cash_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runbook Deltas: "| breaker resets itself when its condition clears | Aditya was told it
    holds until he resets it | the breaker latches in the ledger; `traderbot reset-breaker`
    clears it after `reset` is typed at `/dev/tty` |".
    RULES.md Constraints: "a breaker at 3 settled losses in a row or 10% below the equity peak."

    Expected (from the Deltas line): the breaker holds until Aditya resets it, so after the
    reset a clean card is approved and its order placed.

    The peak is every equity reading ever recorded and a reset leaves it alone, so the gate's
    `breaker_condition` still sees an 11% drawdown and propose or approve re-latches at once.
    With no position (all cash) equity cannot move without an order, and no order passes the
    gate, so `reset-breaker` can never restore trading. The loss streak has the same shape from
    Phase 4 on: no new fill can settle while every order is denied.
    """
    _stub_account(monkeypatch, Decimal(89000))
    monkeypatch.setattr(data, "now_et", lambda: NOW)
    conn = ledger.connect()
    ledger.mark_equity(conn, Decimal(100000), NOW - timedelta(days=1))  # yesterday's peak
    key = ledger.insert_card(conn, a_card(), Decimal("0.01"), ("claude-sonnet-5-5",), gate.Allow())
    first = _approve(key[:8])
    assert isinstance(first, str) and "CIRCUIT_BREAKER" in first, first
    assert ledger.breaker_latched(conn)

    assert ledger.reset_breaker(conn, NOW) == 1  # what `reset-breaker` does once `reset` is typed
    order = _order(key)
    monkeypatch.setattr(broker, "submit_limit_order", lambda trading, card, cid: order)
    assert _approve(key[:8]) is order
