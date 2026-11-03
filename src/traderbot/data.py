"""Daily bars and the market calendar, both read in America/New_York.

`as_of` is the last closed session by the calendar. A session is closed at or after its close
(half days included). A bar the free plan cannot yet serve whole, because SIP history is only
served once it is 15 or more minutes old, is dropped, so a propose in that window reads stale.

Two price series: RAW closes size orders and feed the live gate (`daily_bars`); split and
dividend adjusted closes (`Adjustment.ALL`) feed settlement, desk context and backtests
(`adjusted_daily_bars`, `history`). Calendar reads and complete history windows go through
`cache`; an adjusted window is keyed by the ET date it was fetched, because a later split or
dividend rewrites every adjusted close before it. A window whose `as_of` bar is not served whole
yet is never cached. Every network read retries only transient failures.
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

from traderbot import cache
from traderbot.keychain import AlpacaKeys

ET = ZoneInfo("America/New_York")
SIP_DELAY = timedelta(minutes=16)  # 15 minutes per Alpaca's free plan, plus one spare
LOOKBACK_DAYS = 45  # calendar days, enough for about 30 sessions
CALENDAR_TTL_S = 86_400
RAW_TTL_S = 7 * 86_400  # a closed raw bar does not change
ADJUSTED_TTL_S = 86_400  # and the key carries the fetch date


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
    rows = cache.retry_with_backoff(
        lambda: trading.get_calendar(GetCalendarRequest(start=start, end=end))
    )
    if not isinstance(rows, list):
        raise TypeError("calendar came back raw; the client must return models")
    return [_session(row) for row in rows]


def cached_sessions(trading: TradingClient, start: date, end: date, now: datetime) -> list[Session]:
    """`sessions` through the cache for a day; approve reads the live calendar instead."""
    rows = cache.get_or_fetch(
        "calendar",
        f"{start.isoformat()}:{end.isoformat()}",
        now,
        CALENDAR_TTL_S,
        lambda: [
            [s.day.isoformat(), s.open.isoformat(), s.close.isoformat()]
            for s in sessions(trading, start, end)
        ],
    )
    return [
        Session(
            day=date.fromisoformat(day),
            open=datetime.fromisoformat(opened),
            close=datetime.fromisoformat(closed),
        )
        for day, opened, closed in rows
    ]


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


def next_close(all_sessions: list[Session], now: datetime) -> datetime:
    """The first session close after `now`: when a card proposed at `now` expires."""
    later = [s.close for s in all_sessions if s.close > now]
    if not later:
        raise RuntimeError(f"no session close after {now.isoformat()} in the calendar window")
    return min(later)


def complete_closes(
    raw: list[tuple[date, Decimal]], as_of: Session, data_end: datetime
) -> list[tuple[date, Decimal]]:
    """Bars up to as_of; the as_of bar only when the data window reaches past its close."""
    keep = [(d, c) for d, c in raw if d < as_of.day]
    if data_end >= as_of.close:
        keep += [(d, c) for d, c in raw if d == as_of.day]
    return sorted(keep)


def _fetch_closes(
    keys: AlpacaKeys, symbol: str, start: date, end: datetime, adjustment: Adjustment
) -> list[tuple[date, Decimal]]:
    """Every SIP daily close of `symbol` from `start` to `end`, as Alpaca serves it."""
    client = StockHistoricalDataClient(keys.key_id, keys.secret)
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=datetime.combine(start, datetime.min.time(), ET),
        end=end,
        feed=DataFeed.SIP,
        adjustment=adjustment,
    )
    barset = cache.retry_with_backoff(lambda: client.get_stock_bars(request))
    if not isinstance(barset, BarSet):
        raise TypeError("bars came back raw; the client must return models")
    return [
        (_bar_day(bar.timestamp), Decimal(str(bar.close))) for bar in barset.data.get(symbol, [])
    ]


def history(
    keys: AlpacaKeys,
    symbol: str,
    start: date,
    as_of: Session,
    now: datetime,
    adjustment: Adjustment,
) -> list[tuple[date, Decimal]]:
    """Complete daily closes from `start` through `as_of`, oldest first; cached once whole."""
    data_end = now - SIP_DELAY

    def fetch() -> list[tuple[date, Decimal]]:
        return complete_closes(
            _fetch_closes(keys, symbol, start, data_end, adjustment), as_of, data_end
        )

    if data_end < as_of.close:
        return fetch()  # the as_of bar is not served whole yet: never cache a partial window
    key = f"{symbol}:{adjustment.value}:{start.isoformat()}:{as_of.day.isoformat()}"
    ttl = RAW_TTL_S
    if adjustment != Adjustment.RAW:
        key += f":fetched {now.date().isoformat()}"
        ttl = ADJUSTED_TTL_S
    rows = cache.get_or_fetch(
        "bars",
        key,
        now,
        ttl,
        lambda: [[day.isoformat(), str(close)] for day, close in fetch()],
    )
    return [(date.fromisoformat(day), Decimal(close)) for day, close in rows]


def _bars(
    trading: TradingClient, keys: AlpacaKeys, symbol: str, now: datetime, adjustment: Adjustment
) -> Bars:
    window = cached_sessions(trading, now.date() - timedelta(days=LOOKBACK_DAYS), now.date(), now)
    as_of = last_closed(window, now)
    closes = history(keys, symbol, window[0].day, as_of, now, adjustment)
    if not closes:
        raise RuntimeError(f"no complete daily bars for {symbol} up to {as_of.day}")
    return Bars(symbol=symbol, as_of=as_of.day, bar_date=closes[-1][0], closes=tuple(closes))


def daily_bars(trading: TradingClient, keys: AlpacaKeys, symbol: str, now: datetime) -> Bars:
    """RAW closes for sizing and the gate; `Bars.stale` is true when the as_of bar is missing."""
    return _bars(trading, keys, symbol, now, Adjustment.RAW)


def adjusted_daily_bars(
    trading: TradingClient, keys: AlpacaKeys, symbol: str, now: datetime
) -> Bars:
    """Split and dividend adjusted closes over the same window, for desk context."""
    return _bars(trading, keys, symbol, now, Adjustment.ALL)
