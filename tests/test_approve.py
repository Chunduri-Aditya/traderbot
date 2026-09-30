"""Offline checks on approve, reject and propose: no key, network or model.

Guarded calls (the Keychain, the order submit, the desk) are tripwires derived from BaseException,
so a path that reaches one fails loudly. `live_stubs` stands in for the account: $100k buying
power and equity, no position, no open orders, the clock at 18:00 ET on 2026-09-29.
"""

import sqlite3
import uuid
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from support import NEXT_CLOSE, NOW, SESSIONS, a_card, trip

from traderbot import broker, cli, data, desk, gate, keychain, ledger
from traderbot import proposal as prop


@pytest.fixture
def conn(monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setattr(keychain, "load", trip)
    monkeypatch.setattr(broker, "submit_limit_order", trip)
    return ledger.connect()


@pytest.fixture
def live_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keychain, "load", lambda: keychain.AlpacaKeys("key", "secret"))
    monkeypatch.setattr(broker, "client", lambda keys: object())
    monkeypatch.setattr(broker, "order_by_client_id", lambda trading, client_id: None)
    monkeypatch.setattr(broker, "account", lambda trading: object())
    monkeypatch.setattr(broker, "buying_power", lambda acct: Decimal(100000))
    monkeypatch.setattr(broker, "equity", lambda acct: Decimal(100000))
    monkeypatch.setattr(broker, "position", lambda trading, symbol: (0, Decimal(0)))
    monkeypatch.setattr(broker, "open_order_symbols", lambda trading: frozenset())
    monkeypatch.setattr(data, "now_et", lambda: NOW)
    monkeypatch.setattr(data, "sessions", lambda trading, start, end: SESSIONS)


def store(conn: sqlite3.Connection, card: prop.Proposal, verdict: gate.GateResult) -> str:
    return ledger.insert_card(conn, card, Decimal("0.01"), ("claude-sonnet-5-5",), verdict)


def placed_order(key: str) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(), status=SimpleNamespace(value="accepted"), client_order_id=key
    )


def recording_submit(
    monkeypatch: pytest.MonkeyPatch, order: SimpleNamespace
) -> list[tuple[prop.Proposal, str]]:
    submitted: list[tuple[prop.Proposal, str]] = []

    def submit(trading: object, card: prop.Proposal, client_order_id: str) -> SimpleNamespace:
        submitted.append((card, client_order_id))
        return order

    monkeypatch.setattr(broker, "submit_limit_order", submit)
    return submitted


def decisions(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return conn.execute("SELECT digest, decision FROM decisions").fetchall()


# Refusals before any key read


@pytest.mark.parametrize("prefix", ["abc", "not-hex!", "ffffffff"])
def test_bad_or_unknown_prefix_is_refused_before_any_key_read(
    conn: sqlite3.Connection, prefix: str
) -> None:
    store(conn, a_card(), gate.Allow())
    with pytest.raises(cli.Refused):
        cli.approve(prefix)


def test_tampered_card_is_refused_before_any_key_read(conn: sqlite3.Connection) -> None:
    key = store(conn, a_card(), gate.Allow())
    with conn:
        conn.execute(
            "UPDATE cards SET proposal_json = replace(proposal_json, '\"qty\":1', '\"qty\":50')"
        )
    assert '"qty":50' in conn.execute("SELECT proposal_json FROM cards").fetchone()[0]
    with pytest.raises(cli.Refused, match="altered"):
        cli.approve(key[:8])


def test_card_denied_at_propose_is_refused_before_any_key_read(conn: sqlite3.Connection) -> None:
    key = store(conn, a_card(side="hold"), gate.Deny(("SIDE_NOT_ALLOWED",)))
    with pytest.raises(cli.Refused, match="denied at propose: SIDE_NOT_ALLOWED"):
        cli.approve(key[:8])


def test_rejected_card_is_refused_before_any_key_read(conn: sqlite3.Connection) -> None:
    key = store(conn, a_card(), gate.Allow())
    assert cli.reject(key[:8]) == "reject"
    with pytest.raises(cli.Refused, match="rejected"):
        cli.approve(key[:8])
    assert decisions(conn) == [(key, "reject")]


# The live rerun


def test_rerun_denies_a_swapped_symbol_even_with_the_stored_verdict_edited(
    conn: sqlite3.Connection, live_stubs: None
) -> None:
    key = store(conn, a_card(symbol="QQQ", requested_symbol="SPY"), gate.Deny(("SYMBOL_MISMATCH",)))
    with conn:
        conn.execute('UPDATE cards SET gate_json = \'{"allowed": true, "reasons": []}\'')
    with pytest.raises(cli.Refused, match="SYMBOL_MISMATCH"):
        cli.approve(key[:8])
    assert decisions(conn) == []


def test_card_stored_before_requested_symbol_existed_cannot_be_approved(
    conn: sqlite3.Connection, live_stubs: None
) -> None:
    old_text = prop.canonical(a_card()).replace('"requested_symbol":"SPY",', "")
    assert "requested_symbol" not in old_text
    key = prop.digest_of(old_text)
    with conn:
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                NOW.isoformat(),
                "2026-09-29",
                "SPY",
                "buy",
                old_text,
                "0.01",
                "[]",
                '{"allowed": true, "reasons": []}',
            ),
        )
    with pytest.raises(cli.Refused, match="SYMBOL_MISMATCH"):
        cli.approve(key[:8])


def test_expired_card_is_denied_at_approve(
    conn: sqlite3.Connection, live_stubs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = store(conn, a_card(), gate.Allow())
    monkeypatch.setattr(data, "now_et", lambda: NEXT_CLOSE)
    with pytest.raises(cli.Refused, match="CARD_EXPIRED"):
        cli.approve(key[:8])
    assert decisions(conn) == []


def test_kill_switch_denies_approve_without_a_decision(
    conn: sqlite3.Connection, live_stubs: None
) -> None:
    key = store(conn, a_card(), gate.Allow())
    ledger.set_kill_switch(True)
    with pytest.raises(cli.Refused, match="KILL_SWITCH"):
        cli.approve(key[:8])
    assert decisions(conn) == []


def test_breaker_latches_and_holds_until_reset(
    conn: sqlite3.Connection, live_stubs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = store(conn, a_card(), gate.Allow())
    monkeypatch.setattr(ledger, "consecutive_losses", lambda conn: 3)
    with pytest.raises(cli.Refused, match="CIRCUIT_BREAKER"):
        cli.approve(key[:8])
    assert ledger.breaker_latched(conn)
    monkeypatch.setattr(ledger, "consecutive_losses", lambda conn: 0)
    with pytest.raises(cli.Refused, match="CIRCUIT_BREAKER"):
        cli.approve(key[:8])  # the condition cleared, the latch did not
    ledger.reset_breaker(conn, NOW)
    order = placed_order(key)
    recording_submit(monkeypatch, order)
    assert cli.approve(key[:8]) is order


# Allowed paths


def test_rerun_allows_a_clean_card_records_the_decision_and_the_order(
    conn: sqlite3.Connection, live_stubs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = store(conn, a_card(), gate.Allow())
    order = placed_order(key)
    submitted = recording_submit(monkeypatch, order)
    assert cli.approve(key[:8]) is order
    assert [(c.qty, c.limit_price, cid) for c, cid in submitted] == [(1, Decimal("768.02"), key)]
    card = ledger.find(conn, key)
    assert (card.order_id, card.order_status, card.decision) == (
        str(order.id),
        "accepted",
        "approve",
    )
    kinds = [kind for (kind,) in conn.execute("SELECT kind FROM events ORDER BY seq")]
    assert kinds[-2:] == ["decision", "order"]


def test_second_approve_returns_the_existing_order_and_submits_nothing(
    conn: sqlite3.Connection, live_stubs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = store(conn, a_card(), gate.Allow())
    order = placed_order(key)
    monkeypatch.setattr(broker, "order_by_client_id", lambda trading, client_id: order)
    assert cli.approve(key[:8]) is order  # submit is still the tripwire
    assert decisions(conn) == [(key, "approve")]


def test_approve_then_reject_is_refused(
    conn: sqlite3.Connection, live_stubs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = store(conn, a_card(), gate.Allow())
    recording_submit(monkeypatch, placed_order(key))
    cli.approve(key[:8])
    with pytest.raises(cli.Refused, match="already decided: approve"):
        cli.reject(key[:8])


# Propose


def desk_says(side: Literal["buy", "sell", "hold"], cost: str = "0.0132") -> Any:
    def decide(bars: data.Bars, position_qty: int = 0) -> desk.DeskResult:
        choice = prop.DeskChoice(symbol=bars.symbol, side=side, rationale="r", confidence=0.5)
        return desk.DeskResult(choice, None, Decimal(cost), (desk.MODEL,))

    return decide


@pytest.fixture
def fresh_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = data.Bars(
        symbol="SPY",
        as_of=date(2026, 9, 29),
        bar_date=date(2026, 9, 29),
        closes=((date(2026, 9, 29), Decimal("764.20")),),
    )
    monkeypatch.setattr(data, "daily_bars", lambda trading, keys, symbol, now: bars)


def test_propose_refuses_stale_bars_before_the_desk(
    conn: sqlite3.Connection, live_stubs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = data.Bars(
        symbol="SPY",
        as_of=date(2026, 9, 29),
        bar_date=date(2026, 9, 28),
        closes=((date(2026, 9, 28), Decimal("764.20")),),
    )
    monkeypatch.setattr(data, "daily_bars", lambda trading, keys, symbol, now: stale)
    with pytest.raises(cli.Refused, match="stale"):
        cli.propose("SPY", decide=trip)
    assert ledger.recent(conn) == []


def test_propose_refuses_before_the_desk_while_the_kill_switch_is_on(
    conn: sqlite3.Connection, live_stubs: None, fresh_bars: None
) -> None:
    ledger.set_kill_switch(True)
    with pytest.raises(cli.Refused, match="kill switch"):
        cli.propose("SPY", decide=trip)
    assert ledger.day_spend(conn, NOW.date(), NOW) == 0


def test_propose_refuses_before_the_desk_past_the_spend_cap(
    conn: sqlite3.Connection, live_stubs: None, fresh_bars: None
) -> None:
    ledger.settle(
        conn, ledger.reserve(conn, Decimal("1.80"), Decimal("2.00"), NOW), Decimal("1.80"), NOW
    )
    with pytest.raises(cli.Refused, match="spend"):
        cli.propose("SPY", decide=trip)


def test_propose_settles_the_desk_cost_and_stamps_the_expiry(
    conn: sqlite3.Connection, live_stubs: None, fresh_bars: None
) -> None:
    card = cli.propose("SPY", decide=desk_says("buy"))
    assert card.allowed and card.proposal.expires_at == NEXT_CLOSE
    assert ledger.day_spend(conn, NOW.date(), NOW) == Decimal("0.0132")
    kinds = [kind for (kind,) in conn.execute("SELECT kind FROM events ORDER BY seq")]
    assert "propose" in kinds


def test_a_desk_crash_leaves_the_reservation_held(
    conn: sqlite3.Connection, live_stubs: None, fresh_bars: None
) -> None:
    def crash(bars: data.Bars, position_qty: int = 0) -> desk.DeskResult:
        raise RuntimeError("desk crashed")

    with pytest.raises(RuntimeError, match="desk crashed"):
        cli.propose("SPY", decide=crash)
    assert ledger.day_spend(conn, NOW.date(), NOW) == desk.CALL_CAP_USD


def test_propose_sell_sizes_from_the_position(
    conn: sqlite3.Connection, live_stubs: None, fresh_bars: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(broker, "position", lambda trading, symbol: (3, Decimal("2292.60")))
    card = cli.propose("SPY", decide=desk_says("sell"))
    assert (card.proposal.side, card.proposal.qty, card.proposal.limit_price) == (
        "sell",
        1,
        Decimal("760.38"),
    )
    assert card.allowed


def test_propose_sell_without_a_position_is_denied(
    conn: sqlite3.Connection, live_stubs: None, fresh_bars: None
) -> None:
    card = cli.propose("SPY", decide=desk_says("sell"))
    assert card.reasons == ("QTY_ZERO",)


def test_card_fields_survive_a_round_trip_through_the_ledger(conn: sqlite3.Connection) -> None:
    card = replace(a_card(), side="sell", limit_price=Decimal("760.38"))
    key = store(conn, card, gate.Allow())
    assert ledger.find(conn, key).proposal == card
