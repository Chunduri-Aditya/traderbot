"""Live smoke on a tmp ledger, in two halves, every Alpaca read through this file's own clients.

1. The desk half: `propose SPY` reads live data and asks the real desk. It passes on buy or hold
   (REVIEW fails with the desk's reason) and checks as_of, the close, sizing, the pin and the gate.
2. The order half: `propose SPY` with a buy built by code (no model call), then approve twice.
   The order must read back with the card's digest as its client id, and the second approve must
   return the same order. Teardown cancels it while it is still open.

The split is a runbook delta (2026-09-29): with the order half behind the desk, a desk that held
SPY, IWM and QQQ six times in a row left approve unproven. Traderbot is never checked against its
own helpers: the calendar, the close and the order are all read back here independently.
"""

import uuid
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetCalendarRequest, GetOrdersRequest

from traderbot import cli, keychain
from traderbot.data import Bars
from traderbot.desk import DeskResult
from traderbot.proposal import DeskChoice

pytestmark = pytest.mark.live

ET = ZoneInfo("America/New_York")
PIN = "claude-sonnet-5-5"
SYMBOL = "SPY"
MAX_NOTIONAL = Decimal(1000)  # Aditya's Phase 1 numbers, written here, not read from gate.py
BAND = Decimal("0.005")
PLACED = {"accepted", "new", "pending_new", "partially_filled", "filled"}
DONE = {"filled", "canceled", "expired", "rejected", "pending_cancel"}


@pytest.fixture
def keys() -> keychain.AlpacaKeys:
    return keychain.load()


@pytest.fixture
def alpaca(keys: keychain.AlpacaKeys) -> TradingClient:
    return TradingClient(keys.key_id, keys.secret, paper=True)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADERBOT_HOME", str(tmp_path))
    return tmp_path


def last_closed_session(alpaca: TradingClient, now: datetime) -> date:
    rows = alpaca.get_calendar(
        GetCalendarRequest(start=now.date() - timedelta(days=10), end=now.date())
    )
    assert isinstance(rows, list) and rows
    # The calendar's time zone, read live: naive wall clock, regular sessions open 09:30.
    assert all(r.open.tzinfo is None and r.close.tzinfo is None for r in rows)
    assert any(r.open.time() == time(9, 30) for r in rows)
    return max(r.date for r in rows if r.close.replace(tzinfo=ET) <= now)


def raw_close(keys: keychain.AlpacaKeys, day: date, now: datetime) -> Decimal:
    request = StockBarsRequest(
        symbol_or_symbols=SYMBOL,
        timeframe=TimeFrame.Day,
        start=datetime.combine(day - timedelta(days=5), time(0), ET),
        end=now - timedelta(minutes=16),
        feed=DataFeed.SIP,
        adjustment=Adjustment.RAW,
    )
    bars = StockHistoricalDataClient(keys.key_id, keys.secret).get_stock_bars(request)
    closes = [b.close for b in bars.data[SYMBOL] if b.timestamp.astimezone(ET).date() == day]
    assert len(closes) == 1, f"want one {SYMBOL} bar on {day}, got {len(closes)}"
    return Decimal(str(closes[0]))


def spec_size(close: Decimal) -> tuple[Decimal, int]:
    """The spec's sizing: limit floored to the cent above $1, qty = floor(max notional / limit)."""
    limit = (close * (1 + BAND) / Decimal("0.01")).to_integral_value(ROUND_FLOOR) * Decimal("0.01")
    return limit, int((MAX_NOTIONAL / limit).to_integral_value(ROUND_FLOOR))


def orders_with_client_id(alpaca: TradingClient, client_id: str, since: datetime) -> list[object]:
    request = GetOrdersRequest(
        status=QueryOrderStatus.ALL, symbols=[SYMBOL], after=since, limit=500
    )
    orders = alpaca.get_orders(request)
    assert isinstance(orders, list)
    return [o for o in orders if o.client_order_id == client_id]


@pytest.fixture
def placed_client_ids(alpaca: TradingClient) -> Iterator[list[str]]:
    client_ids: list[str] = []
    yield client_ids
    for client_id in client_ids:
        try:
            order = alpaca.get_order_by_client_id(client_id)
        except APIError as err:
            if err.status_code == 404:
                continue
            raise
        if order.status.value not in DONE:
            alpaca.cancel_order_by_id(order.id)


def code_built_buy(bars: Bars) -> DeskResult:
    choice = DeskChoice(
        symbol=bars.symbol,
        side="buy",
        rationale="live test: buy built by code, no model call",
        confidence=0.5,
    )
    return DeskResult(choice=choice, review_reason=None, cost_usd=Decimal(0), model_usage=())


def test_desk_half_reads_live_data_and_the_pinned_model(
    home: Path, keys: keychain.AlpacaKeys, alpaca: TradingClient
) -> None:
    account = alpaca.get_account()
    assert account.account_number.startswith("PA"), "not the paper account prefix"
    started = datetime.now(ET)
    expected_as_of = last_closed_session(alpaca, started)

    card = cli.propose(SYMBOL)

    if card.proposal.side not in ("buy", "hold"):
        pytest.fail(f"desk gave {card.proposal.side}: {card.proposal.rationale}")
    assert card.proposal.as_of == expected_as_of
    assert card.proposal.bar_date == expected_as_of
    assert len(card.digest) == 64 and all(ch in "0123456789abcdef" for ch in card.digest)
    assert card.model_usage == (PIN,)
    assert Decimal(0) < card.total_cost_usd <= Decimal("0.25")
    close = raw_close(keys, expected_as_of, started)
    assert card.proposal.last_close == close
    assert (card.proposal.limit_price, card.proposal.qty) == spec_size(close)
    if card.proposal.side == "buy":
        assert card.allowed, f"gate denied a buy: {card.reasons}"
    else:
        assert card.reasons == ("SIDE_NOT_ALLOWED",)


def test_order_half_places_one_paper_order_and_approve_is_idempotent(
    home: Path, alpaca: TradingClient, placed_client_ids: list[str]
) -> None:
    started = datetime.now(ET)

    card = cli.propose(SYMBOL, decide=code_built_buy)

    assert card.proposal.side == "buy"
    assert card.model_usage == () and card.total_cost_usd == 0
    assert card.allowed, f"gate denied at propose: {card.reasons}"

    placed_client_ids.append(card.digest)
    first = cli.approve(card.digest[:8])

    read = alpaca.get_order_by_client_id(card.digest)
    assert isinstance(read.id, uuid.UUID)
    assert read.id == first.id
    assert read.client_order_id == card.digest
    assert read.status.value in PLACED
    assert read.symbol == SYMBOL and read.side.value == "buy"
    assert read.type.value == "limit" and read.time_in_force.value == "day"
    assert Decimal(read.qty) == card.proposal.qty
    assert Decimal(read.limit_price) == card.proposal.limit_price

    second = cli.approve(card.digest[:8])
    assert second.id == first.id
    assert len(orders_with_client_id(alpaca, card.digest, started - timedelta(minutes=5))) == 1
