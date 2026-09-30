"""Live smoke on a tmp ledger, in two halves, every Alpaca read through this file's own clients.

1. The desk half: `propose SPY` reads live data and asks the real desk. It passes on buy or hold
   (REVIEW fails with the desk's reason) and checks as_of, the close, sizing, the pin and the gate.
2. The order half: `propose SPY` with a buy built by code (no model call), then approve twice.
   The order must read back with the card's digest as its client id, and the second approve must
   return the same order. Teardown cancels it while it is still open.

The split is a runbook delta (2026-09-29): with the order half behind the desk, a desk that held
SPY, IWM and QQQ six times in a row left approve unproven. Traderbot is never checked against its
own helpers: the calendar, the close and the order are all read back here independently.

Run it after the close. From a session close until 16 minutes later the newest bar is not served
whole yet, so propose refuses stale bars and both halves fail. During a session the limit sits
above the last close and can fill at once, which leaves about $1,000 of SPY on the paper account;
teardown cancels open orders only and never sells.
"""

import uuid
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alpaca.common.exceptions import APIError
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.models.bars import BarSet
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.models import Order, Position, TradeAccount
from alpaca.trading.requests import GetCalendarRequest, GetOrdersRequest

from traderbot import cli, keychain, ledger
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
OPEN = {"accepted", "new", "pending_new", "partially_filled", "held", "accepted_for_bidding"}


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
    assert isinstance(bars, BarSet)
    closes = [b.close for b in bars.data[SYMBOL] if b.timestamp.astimezone(ET).date() == day]
    assert len(closes) == 1, f"want one {SYMBOL} bar on {day}, got {len(closes)}"
    return Decimal(str(closes[0]))


def spec_size(close: Decimal, side: str, held: int) -> tuple[Decimal, int]:
    """The spec's sizing above $1: a buy (or a hold, as a counterfactual buy) floors the limit
    to the cent above the close, a sell ceils it to the cent below; qty = floor(max / limit),
    and a sell never exceeds the shares held."""
    cent = Decimal("0.01")
    if side == "sell":
        limit = (close * (1 - BAND) / cent).to_integral_value(ROUND_CEILING) * cent
        return limit, min(held, int((MAX_NOTIONAL / limit).to_integral_value(ROUND_FLOOR)))
    limit = (close * (1 + BAND) / cent).to_integral_value(ROUND_FLOOR) * cent
    return limit, int((MAX_NOTIONAL / limit).to_integral_value(ROUND_FLOOR))


def shares_held(alpaca: TradingClient) -> int:
    try:
        held = alpaca.get_open_position(SYMBOL)
    except APIError as err:
        if err.status_code == 404:
            return 0
        raise
    assert isinstance(held, Position)
    return int(Decimal(held.qty))


def order_read(alpaca: TradingClient, client_id: str) -> Order:
    order = alpaca.get_order_by_client_id(client_id)
    assert isinstance(order, Order)
    return order


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
            order = order_read(alpaca, client_id)
        except APIError as err:
            if err.status_code == 404:
                continue
            raise
        if order.status.value in OPEN:
            alpaca.cancel_order_by_id(order.id)


def code_built_buy(bars: Bars, position_qty: int = 0) -> DeskResult:
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
    assert isinstance(account, TradeAccount)
    assert account.account_number.startswith("PA"), "not the paper account prefix"
    started = datetime.now(ET)
    expected_as_of = last_closed_session(alpaca, started)
    held = shares_held(alpaca)

    card = cli.propose(SYMBOL)

    side = card.proposal.side
    if side not in ("buy", "sell", "hold"):
        pytest.fail(f"desk gave {side}: {cli.shown(card.proposal.rationale)}")
    assert card.proposal.as_of == expected_as_of
    assert card.proposal.bar_date == expected_as_of
    assert len(card.digest) == 64 and all(ch in "0123456789abcdef" for ch in card.digest)
    assert card.model_usage == (PIN,)
    assert Decimal(0) < card.total_cost_usd <= Decimal("0.25")
    close = raw_close(keys, expected_as_of, started)
    assert card.proposal.last_close == close
    assert (card.proposal.limit_price, card.proposal.qty) == spec_size(close, side, held)
    if side == "hold":
        assert card.reasons == ("SIDE_NOT_ALLOWED",)
    elif side == "sell" and held == 0:
        assert card.reasons == ("QTY_ZERO",)
    else:
        assert card.allowed, f"gate denied a {side}: {card.reasons}"


def test_order_half_places_one_paper_order_and_approve_is_idempotent(
    home: Path, alpaca: TradingClient, placed_client_ids: list[str]
) -> None:
    started = datetime.now(ET)

    card = cli.propose(SYMBOL, decide=code_built_buy, desk_label="test")

    assert card.proposal.side == "buy"
    assert card.model_usage == () and card.total_cost_usd == 0
    assert card.allowed, f"gate denied at propose: {card.reasons}"

    placed_client_ids.append(card.digest)
    first = cli.approve(card.digest[:8])

    read = order_read(alpaca, card.digest)
    assert isinstance(read.id, uuid.UUID)
    assert read.id == first.id
    assert read.client_order_id == card.digest
    assert read.status.value in PLACED
    assert read.symbol == SYMBOL and read.side is not None and read.side.value == "buy"
    assert read.type is not None and read.type.value == "limit"
    assert read.time_in_force.value == "day"
    assert Decimal(str(read.qty)) == card.proposal.qty
    assert Decimal(str(read.limit_price)) == card.proposal.limit_price

    stored = ledger.find(ledger.connect(), card.digest)
    assert (stored.order_id, stored.order_status) == (str(first.id), first.status.value)

    second = cli.approve(card.digest[:8])
    assert second.id == first.id
    assert len(orders_with_client_id(alpaca, card.digest, started - timedelta(minutes=5))) == 1
