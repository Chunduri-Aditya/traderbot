"""Offline checks on approve's refusal paths and propose's stale guard: no key, network or model.

Every guarded call (the Keychain, the order submit, the desk) is a tripwire derived from
BaseException, so an `except Exception` on the way cannot swallow it and a path that reaches
it fails loudly instead of passing.
"""

import sqlite3
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from traderbot import broker, cli, data, gate, keychain, ledger
from traderbot import proposal as prop

ET = data.ET
AS_OF = date(2026, 9, 29)
NOW = datetime(2026, 9, 29, 18, 0, tzinfo=ET)  # after that session's 16:00 close
SESSION = data.Session(
    day=AS_OF,
    open=datetime(2026, 9, 29, 9, 30, tzinfo=ET),
    close=datetime(2026, 9, 29, 16, 0, tzinfo=ET),
)


class Tripwire(BaseException):
    """A guarded call ran that the path under test must never reach."""


def trip(*args: Any, **kwargs: Any) -> Any:
    raise Tripwire("a guarded call ran")


def a_card(symbol: str = "SPY", requested: str = "SPY", side: str = "buy") -> prop.Proposal:
    return prop.Proposal(
        symbol=symbol,
        requested_symbol=requested,
        side=side,
        rationale="offline test card",
        confidence=0.5,
        qty=1,
        limit_price=Decimal("768.02"),
        order_type="limit",
        time_in_force="day",
        as_of=AS_OF,
        bar_date=AS_OF,
        last_close=Decimal("764.20"),
        desk="single",
        model="claude-sonnet-5-5",
        created_at=NOW,
    )


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    monkeypatch.setenv(ledger.HOME_ENV, str(tmp_path))
    monkeypatch.setattr(keychain, "load", trip)
    monkeypatch.setattr(broker, "submit_limit_buy", trip)
    return ledger.connect()


@pytest.fixture
def live_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Account state for the approve rerun: $100k buying power, no open orders, clock at NOW."""
    monkeypatch.setattr(keychain, "load", lambda: keychain.AlpacaKeys("key", "secret"))
    monkeypatch.setattr(broker, "client", lambda keys: object())
    monkeypatch.setattr(broker, "order_by_client_id", lambda trading, client_id: None)
    monkeypatch.setattr(broker, "account", lambda trading: object())
    monkeypatch.setattr(broker, "buying_power", lambda acct: Decimal(100000))
    monkeypatch.setattr(broker, "open_order_symbols", lambda trading: frozenset())
    monkeypatch.setattr(data, "now_et", lambda: NOW)
    monkeypatch.setattr(data, "sessions", lambda trading, start, end: [SESSION])


def store(conn: sqlite3.Connection, card: prop.Proposal, verdict: gate.GateResult) -> str:
    return ledger.insert_card(conn, card, Decimal("0.01"), ("claude-sonnet-5-5",), verdict)


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


def test_rerun_denies_a_swapped_symbol_even_with_the_stored_verdict_edited(
    conn: sqlite3.Connection, live_stubs: None
) -> None:
    key = store(conn, a_card(symbol="QQQ", requested="SPY"), gate.Deny(("SYMBOL_MISMATCH",)))
    with conn:
        conn.execute('UPDATE cards SET gate_json = \'{"allowed": true, "reasons": []}\'')
    with pytest.raises(cli.Refused, match="SYMBOL_MISMATCH"):
        cli.approve(key[:8])


def test_card_stored_before_requested_symbol_existed_cannot_be_approved(
    conn: sqlite3.Connection, live_stubs: None
) -> None:
    fields = prop.canonical(a_card())
    old_text = fields.replace('"requested_symbol":"SPY",', "")
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


def test_rerun_allows_a_clean_card_and_records_the_order(
    conn: sqlite3.Connection, live_stubs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = store(conn, a_card(), gate.Allow())
    placed = SimpleNamespace(
        id=uuid.uuid4(), status=SimpleNamespace(value="accepted"), client_order_id=key
    )
    submitted: list[tuple[prop.Proposal, str]] = []

    def submit(trading: object, card: prop.Proposal, client_order_id: str) -> SimpleNamespace:
        submitted.append((card, client_order_id))
        return placed

    monkeypatch.setattr(broker, "submit_limit_buy", submit)
    assert cli.approve(key[:8]) is placed
    assert [(c.qty, c.limit_price, cid) for c, cid in submitted] == [(1, Decimal("768.02"), key)]
    stored = ledger.find(conn, key)
    assert (stored.order_id, stored.order_status) == (str(placed.id), "accepted")


def test_propose_refuses_stale_bars_before_the_desk(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(keychain, "load", lambda: keychain.AlpacaKeys("key", "secret"))
    monkeypatch.setattr(broker, "client", lambda keys: object())
    monkeypatch.setattr(data, "now_et", lambda: NOW)
    stale = data.Bars(
        symbol="SPY",
        as_of=AS_OF,
        bar_date=date(2026, 9, 28),
        closes=((date(2026, 9, 28), Decimal("764.20")),),
    )
    monkeypatch.setattr(data, "daily_bars", lambda trading, keys, symbol, now: stale)
    with pytest.raises(cli.Refused, match="stale"):
        cli.propose("SPY", decide=trip)
    assert ledger.recent(conn) == []
