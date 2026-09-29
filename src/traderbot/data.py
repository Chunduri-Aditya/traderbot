"""Daily bars and the market calendar, both read in America/New_York.

`as_of` is the last closed session by the calendar. A session is closed at or after its close
(half days included). A bar the free plan cannot yet serve whole, because SIP history is only
served once it is 15 or more minutes old, is dropped, so a propose in that window reads stale.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.models.bars import BarSet
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.models import Calendar
from alpaca.trading.requests import GetCalendarRequest

from traderbot.keychain import AlpacaKeys

ET = ZoneInfo("America/New_York")
SIP_DELAY = timedelta(minutes=16)  # 15 minutes per Alpaca's free plan, plus one spare
LOOKBACK_DAYS = 45  # calendar days, enough for about 30 sessions


def now_et() -> datetime:
    return datetime.now(ET)


def to_et(moment: datetime) -> datetime:
    """Calendar times come back naive (alpaca-py parses "%Y-%m-%d %H:%M"); they are ET."""
    return moment.replace(tzinfo=ET) if moment.tzinfo is None else moment.astimezone(ET)


@dataclass(frozen=True)
class Session:
    day: date
    open: datetime
    close: datetime


@dataclass(frozen=True)
class Bars:
    symbol: str
    as_of: date  # last closed session by the calendar
    bar_date: date  # date of the latest complete bar
    closes: tuple[tuple[date, Decimal], ...]  # complete bars, oldest first, raw prices

    @property
    def last_close(self) -> Decimal:
        return self.closes[-1][1]

    @property
    def stale(self) -> bool:
        return self.bar_date != self.as_of


def sessions(trading: TradingClient, start: date, end: date) -> list[Session]:
    rows = trading.get_calendar(GetCalendarRequest(start=start, end=end))
    if not isinstance(rows, list):
        raise TypeError("calendar came back raw; the client must return models")
    return [_session(row) for row in rows]


def _session(row: Calendar) -> Session:
    return Session(day=row.date, open=to_et(row.open), close=to_et(row.close))


def _bar_day(stamp: datetime) -> date:
    """A daily bar is stamped at ET midnight in UTC; naive stamps are UTC per alpaca-py."""
    aware = stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp
    return aware.astimezone(ET).date()


def last_closed(all_sessions: list[Session], now: datetime) -> Session:
    closed = [s for s in all_sessions if s.close <= now]
    if not closed:
        raise RuntimeError(f"no closed session in the calendar window before {now.isoformat()}")
    return max(closed, key=lambda s: s.close)


def complete_closes(
    raw: list[tuple[date, Decimal]], as_of: Session, data_end: datetime
) -> list[tuple[date, Decimal]]:
    """Bars up to as_of; the as_of bar only when the data window reaches past its close."""
    keep = [(d, c) for d, c in raw if d < as_of.day]
    if data_end >= as_of.close:
        keep += [(d, c) for d, c in raw if d == as_of.day]
    return sorted(keep)


def daily_bars(trading: TradingClient, keys: AlpacaKeys, symbol: str, now: datetime) -> Bars:
    window = sessions(trading, now.date() - timedelta(days=LOOKBACK_DAYS), now.date())
    as_of = last_closed(window, now)
    data_end = now - SIP_DELAY
    client = StockHistoricalDataClient(keys.key_id, keys.secret)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.combine(window[0].day, datetime.min.time(), ET),
        end=data_end,
        feed=DataFeed.SIP,
        adjustment=Adjustment.RAW,
    )
    barset = client.get_stock_bars(request)
    if not isinstance(barset, BarSet):
        raise TypeError("bars came back raw; the client must return models")
    raw = [
        (_bar_day(bar.timestamp), Decimal(str(bar.close))) for bar in barset.data.get(symbol, [])
    ]
    closes = complete_closes(raw, as_of, data_end)
    if not closes:
        raise RuntimeError(f"no complete daily bars for {symbol} up to {as_of.day}")
    return Bars(symbol=symbol, as_of=as_of.day, bar_date=closes[-1][0], closes=tuple(closes))
