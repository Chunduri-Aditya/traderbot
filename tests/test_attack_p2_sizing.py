"""Attack lens: sizing, rounding and limits (traderbot Phase 2, HEAD 98b2fa3).

Offline only. conftest.py (copied from tests/) makes claude, the Keychain and every HTTP request
tripwires and points TRADERBOT_HOME at tmp_path. Every expected value below is taken from the
quoted spec, runbook or RULES.md line, never from the code under test.
"""

import sqlite3
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.trading.enums import AssetClass, AssetExchange, PositionSide
from alpaca.trading.models import Position
from support import LIMITS, NOW, SESSIONS, a_card, a_snapshot

from traderbot import broker, cli, data, gate, keychain, ledger
from traderbot import proposal as prop


class FakePaperTrading:
    """A stand in client on the paper host whose only live answer is the position read."""

    _base_url = broker.PAPER_URL

    def __init__(self, held: Position) -> None:
        self.held = held

    def get_open_position(self, symbol: str) -> Position:
        return self.held


@pytest.fixture
def account_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """$100,000 equity and buying power, no open orders, the clock at 18:00 ET on 2026-09-29."""
    monkeypatch.setattr(keychain, "load", lambda: keychain.AlpacaKeys("key", "secret"))
    monkeypatch.setattr(broker, "order_by_client_id", lambda trading, client_id: None)
    monkeypatch.setattr(broker, "account", lambda trading: object())
    monkeypatch.setattr(broker, "buying_power", lambda acct: Decimal(100000))
    monkeypatch.setattr(broker, "equity", lambda acct: Decimal(100000))
    monkeypatch.setattr(broker, "open_order_symbols", lambda trading: frozenset())
    monkeypatch.setattr(data, "now_et", lambda: NOW)
    monkeypatch.setattr(data, "sessions", lambda trading, start, end: SESSIONS)


def recording_submit(monkeypatch: pytest.MonkeyPatch) -> list[prop.Proposal]:
    submitted: list[prop.Proposal] = []

    def submit(trading: object, card: prop.Proposal, client_order_id: str) -> Any:
        submitted.append(card)
        return SimpleNamespace(
            id=uuid.uuid4(),
            status=SimpleNamespace(value="accepted"),
            client_order_id=client_order_id,
        )

    monkeypatch.setattr(broker, "submit_limit_order", submit)
    return submitted


def test_position_share_counts_a_held_position_whose_market_value_is_missing(
    account_stubs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec, Phase 2 gate rules: "`POSITION_OVER_MAX_PCT`: a symbol's share of equity;"
    RULES.md Constraints: "10% max position of equity".

    Expected from those lines: 10% of the $100,000 equity is $10,000. The account holds 20 SPY
    shares; at the card's own last close of $764.20 that is $15,284, already over $10,000 before
    the $768.02 buy, so approve must not submit. alpaca-py 0.44.0 declares
    `Position.market_value: Optional[str] = None`, and `broker.position` maps a missing value to
    `Decimal(0)` (`Decimal(held.market_value or 0)`), so the gate sees a $0 position and allows.
    """
    held = Position(
        asset_id=uuid.uuid4(),
        symbol="SPY",
        exchange=AssetExchange.ARCA,
        asset_class=AssetClass.US_EQUITY,
        avg_entry_price="700.00",
        qty="20",
        side=PositionSide.LONG,
        market_value=None,
        cost_basis="14000.00",
    )
    monkeypatch.setattr(broker, "client", lambda keys: FakePaperTrading(held))
    submitted = recording_submit(monkeypatch)
    conn = ledger.connect()
    key = ledger.insert_card(conn, a_card(), Decimal("0.01"), ("claude-sonnet-5-5",), gate.Allow())
    try:
        cli.approve(key[:8])
    except (cli.Refused, RuntimeError):
        pass  # a POSITION_OVER_MAX_PCT deny, or a refusal to read a position with no value
    assert submitted == [], "a buy was submitted while 20 held shares counted as a $0 position"


def test_gate_returns_a_deny_for_the_card_an_infinite_close_sizes() -> None:
    """Spec, Phase 1 step 4: "`gate.py`: a pure function over (Proposal, AccountSnapshot, Limits,
    now) that returns Allow or Deny(reasons)." Spec: "qty > 0 and notional <= max;" Runbook
    Phase 1 Sizing: "The gate checks `limit > 0`, `qty > 0` and `qty * limit <= max_notional`."

    Expected from those lines: a card with qty 0 is a Deny carrying QTY_ZERO. An infinite close
    (Decimal(str(float('inf'))) in data.daily_bars) sizes to limit Infinity and qty 0; the gate
    then computes `0 * Infinity` for the notional and raises decimal.InvalidOperation instead of
    returning the Deny.
    """
    limit, qty = prop.size(Decimal("Infinity"), LIMITS.max_notional, LIMITS.band)
    card = a_card(qty=qty, limit_price=limit, last_close=Decimal("Infinity"))
    result = gate.check(card, "SPY", a_snapshot(), LIMITS, NOW)
    assert isinstance(result, gate.Deny)
    assert "QTY_ZERO" in result.reasons


def test_status_header_shows_the_day_spend_without_a_float_round_trip(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """RULES.md Do: "Money is `Decimal` end to end; convert to float only at the `alpaca-py`
    request boundary."

    `cli._readable` prints the status header with `float(view['spend_usd']):.4f`. A settled desk
    cost of exactly $0.00015 is, at four places, $0.0002 under both half up and half even
    rounding; the float 0.00015 is 0.000149999999999999986..., so the header prints $0.0001.
    Expected from the RULES line (Decimal to the display): "$0.0002 of $2.00".
    """
    monkeypatch.setattr(data, "now_et", lambda: NOW)
    conn: sqlite3.Connection = ledger.connect()
    rid = ledger.reserve(conn, Decimal("0.25"), Decimal("2.00"), NOW)
    ledger.settle(conn, rid, Decimal("0.00015"), NOW)
    assert cli.main(["status"]) == 0
    header = capsys.readouterr().out.splitlines()[0]
    assert "$0.0002 of $2.00" in header, header
