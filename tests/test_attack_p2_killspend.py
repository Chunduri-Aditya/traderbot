"""Attack lens: the kill switch and spend reservations (traderbot Phase 2, HEAD 98b2fa3).

Offline only. conftest.py makes claude, the Keychain and HTTP tripwires and points
TRADERBOT_HOME at tmp_path. Every trading client here is a fake on the paper host that records a
submit and raises `Submitted`, so a submit that should have been refused shows up as a recorded
request, never as a real order.
"""

import contextlib
import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any, cast

import pytest
from alpaca.trading.client import TradingClient
from support import NOW, SESSIONS, a_card

from traderbot import broker, cli, data, desk, gate, keychain, ledger
from traderbot import proposal as prop

BARS = data.Bars(
    symbol="SPY",
    as_of=date(2026, 9, 29),
    bar_date=date(2026, 9, 29),
    closes=((date(2026, 9, 29), Decimal("764.20")),),
)


class Submitted(Exception):
    """The fake client received an order request."""


class FakeTrading:
    """A client on the paper host that records every submit instead of placing it."""

    _base_url = broker.PAPER_URL

    def __init__(self) -> None:
        self.submitted: list[object] = []

    def submit_order(self, request: object) -> object:
        self.submitted.append(request)
        raise Submitted("an order request reached the trading client")


def as_client(fake: FakeTrading) -> TradingClient:
    return cast(TradingClient, fake)


@pytest.fixture
def conn() -> sqlite3.Connection:
    return ledger.connect()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeTrading:
    """The account behind propose and approve: $100k, no position, no open orders, 18:00 ET."""
    client = FakeTrading()
    monkeypatch.setattr(keychain, "load", lambda: keychain.AlpacaKeys("key", "secret"))
    monkeypatch.setattr(broker, "client", lambda keys: client)
    monkeypatch.setattr(broker, "order_by_client_id", lambda trading, client_id: None)
    monkeypatch.setattr(broker, "account", lambda trading: object())
    monkeypatch.setattr(broker, "buying_power", lambda acct: Decimal(100000))
    monkeypatch.setattr(broker, "equity", lambda acct: Decimal(100000))
    monkeypatch.setattr(broker, "position", lambda trading, symbol: (0, Decimal(0)))
    monkeypatch.setattr(broker, "open_order_symbols", lambda trading: frozenset())
    monkeypatch.setattr(data, "now_et", lambda: NOW)
    monkeypatch.setattr(data, "sessions", lambda trading, start, end: SESSIONS)
    monkeypatch.setattr(data, "daily_bars", lambda trading, keys, symbol, now: BARS)
    return client


def buy(bars: data.Bars) -> desk.DeskResult:
    choice = prop.DeskChoice(symbol=bars.symbol, side="buy", rationale="r", confidence=0.5)
    return desk.DeskResult(choice, None, Decimal("0.0132"), (desk.MODEL,))


def decisions(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return conn.execute("SELECT digest, decision FROM decisions").fetchall()


# The kill switch read by propose


def test_off_during_the_desk_call_still_stores_an_allowed_card(
    conn: sqlite3.Connection, fake: FakeTrading
) -> None:
    """Spec, Phase 2: "**Kill switch**: `traderbot off` and `traderbot on` create and remove
    `~/.traderbot/OFF`. The gate and `broker.py` both refuse while it exists."

    Expected from that line: a gate verdict computed while OFF exists is a refusal, so no card
    is stored as allowed. propose reads the switch into its snapshot before the desk call (up to
    300 s) and refreshes only `day_spend_usd` afterwards, so an OFF created during the call is
    missed and the stored card says ALLOW (and the readable view prints the approve line).
    """

    def desk_then_off(bars: data.Bars, position_qty: int = 0) -> desk.DeskResult:
        ledger.set_kill_switch(True)  # Aditya runs `traderbot off` while the desk is thinking
        return buy(bars)

    with contextlib.suppress(cli.Refused):
        cli.propose("SPY", decide=desk_then_off)
    assert ledger.kill_switch_on()
    allowed = [(card.digest[:8], card.reasons) for card in ledger.recent(conn) if card.allowed]
    assert allowed == [], f"cards stored as ALLOW while OFF exists: {allowed}"


def test_off_seen_by_the_snapshot_still_pays_for_a_desk_call(
    conn: sqlite3.Connection, fake: FakeTrading, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runbook Deltas: "| propose on stale bars | the desk was paid for a card the gate must
    deny `STALE_BARS` | propose refuses before the desk call |". ARCHITECTURE Flow: "propose:
    kill switch check, `keychain` keys, `data` (calendar sets `as_of`, SIP daily bars; stale
    refuses), account snapshot (a tripped breaker refuses), a spend reservation of the $0.25 call
    cap, `desk`".

    Expected: $0 of day spend and no desk call when the snapshot taken before the reservation
    already reads the switch on, because the gate must then deny `KILL_SWITCH`. propose checks
    the file only at its first line and never acts on `snapshot.kill_switch` before reserving.
    """
    calls: list[str] = []

    def bars_then_off(trading: Any, keys: Any, symbol: str, now: Any) -> data.Bars:
        ledger.set_kill_switch(True)  # `traderbot off` lands while the bars load
        return BARS

    def counted_desk(bars: data.Bars, position_qty: int = 0) -> desk.DeskResult:
        calls.append(bars.symbol)
        return buy(bars)

    monkeypatch.setattr(data, "daily_bars", bars_then_off)
    with contextlib.suppress(cli.Refused):
        cli.propose("SPY", decide=counted_desk)
    assert calls == [], "the desk was called after the snapshot read the kill switch on"
    assert ledger.day_spend(conn, NOW.date(), NOW) == Decimal(0)


# The kill switch at submit


def test_off_between_the_live_gate_and_the_submit_leaves_a_standing_approve(
    conn: sqlite3.Connection, fake: FakeTrading, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runbook Deltas: "| approve records its decision first | a gate deny would leave an approve
    that later blocks a reject | the decision is recorded after the live gate passes, then the
    order is submitted; a second approve finds the order by client id |". RULES.md Errors: "A
    deliberate stop (spend cap, stale bars, tamper, gate deny, bad prefix) is `cli.Refused`: one
    line on stderr, exit 1."

    Expected: a refusal that places no order leaves no decision row, and it is a `cli.Refused`.
    broker's own kill switch check runs after `_decide`, so an OFF that lands after the live gate
    leaves "approve" standing with no order (reject is then refused and `approve` no longer lists
    the card) and escapes as `broker.KillSwitchOn` with a traceback.
    """
    key = ledger.insert_card(conn, a_card(), Decimal("0.01"), (desk.MODEL,), gate.Allow())
    real_check = gate.check

    def check_then_off(*args: Any, **kwargs: Any) -> gate.GateResult:
        verdict = real_check(*args, **kwargs)
        ledger.set_kill_switch(True)  # `traderbot off` typed in another terminal right now
        return verdict

    monkeypatch.setattr(gate, "check", check_then_off)
    raised: BaseException | None = None
    try:
        cli.approve(key[:8])
    except (cli.Refused, broker.KillSwitchOn, Submitted) as err:
        raised = err
    assert fake.submitted == []
    assert decisions(conn) == [], "an approve stands for a card whose order was never placed"
    assert isinstance(raised, cli.Refused), f"kill switch stop escaped as {raised!r}"
