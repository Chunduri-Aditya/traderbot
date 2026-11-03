"""Calendar and bars offline: as_of by the calendar, the SIP delay, staleness, and which windows
the cache may keep."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from alpaca.data.enums import Adjustment
from support import AS_OF, ET, NOW, SESSIONS, Tripwire

from traderbot import data, keychain

KEYS = keychain.AlpacaKeys("key", "secret")
CLOSE = SESSIONS[0].close
ROWS = [(date(2026, 9, 28), Decimal("760.00")), (AS_OF, Decimal("764.20"))]


class Fetches:
    def __init__(self, rows: list[tuple[date, Decimal]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, Adjustment, datetime]] = []

    def __call__(
        self, keys: Any, symbol: str, start: date, end: datetime, adjustment: Adjustment
    ) -> list[tuple[date, Decimal]]:
        self.calls.append((symbol, adjustment, end))
        return [(d, c) for d, c in self.rows if datetime.combine(d, datetime.min.time(), ET) <= end]


@pytest.fixture
def fetches(monkeypatch: pytest.MonkeyPatch) -> Fetches:
    fake = Fetches(ROWS)
    monkeypatch.setattr(data, "_fetch_closes", fake)
    monkeypatch.setattr(data, "sessions", lambda trading, start, end: SESSIONS)
    return fake


def test_last_closed_counts_a_session_from_its_close() -> None:
    assert data.last_closed(SESSIONS, CLOSE).day == AS_OF
    with pytest.raises(RuntimeError, match="no closed session"):
        data.last_closed(SESSIONS, CLOSE - timedelta(seconds=1))


def test_the_as_of_bar_counts_only_once_the_data_window_reaches_its_close() -> None:
    assert data.complete_closes(ROWS, SESSIONS[0], CLOSE - timedelta(minutes=1)) == ROWS[:1]
    assert data.complete_closes(ROWS, SESSIONS[0], CLOSE) == ROWS


def test_bars_sixteen_minutes_after_the_close_are_fresh(fetches: Fetches) -> None:
    bars = data.daily_bars(object(), KEYS, "SPY", CLOSE + timedelta(minutes=16))  # type: ignore[arg-type]
    assert not bars.stale and bars.bar_date == AS_OF and bars.last_close == Decimal("764.20")


def test_bars_fifteen_minutes_after_the_close_are_stale_and_not_cached(fetches: Fetches) -> None:
    early = CLOSE + timedelta(minutes=15)
    assert data.daily_bars(object(), KEYS, "SPY", early).stale  # type: ignore[arg-type]
    data.daily_bars(object(), KEYS, "SPY", early)  # type: ignore[arg-type]
    assert len(fetches.calls) == 2  # a partial window is fetched every time


def test_a_complete_raw_window_is_cached(fetches: Fetches) -> None:
    data.daily_bars(object(), KEYS, "SPY", NOW)  # type: ignore[arg-type]
    data.daily_bars(object(), KEYS, "SPY", NOW + timedelta(hours=1))  # type: ignore[arg-type]
    assert [call[1] for call in fetches.calls] == [Adjustment.RAW]


def test_raw_and_adjusted_are_separate_series(fetches: Fetches) -> None:
    data.daily_bars(object(), KEYS, "SPY", NOW)  # type: ignore[arg-type]
    data.adjusted_daily_bars(object(), KEYS, "SPY", NOW)  # type: ignore[arg-type]
    assert [call[1] for call in fetches.calls] == [Adjustment.RAW, Adjustment.ALL]


def test_adjusted_history_is_fetched_again_the_next_day(fetches: Fetches) -> None:
    as_of = SESSIONS[0]
    data.history(KEYS, "SPY", date(2026, 9, 1), as_of, NOW, Adjustment.ALL)
    data.history(KEYS, "SPY", date(2026, 9, 1), as_of, NOW + timedelta(hours=2), Adjustment.ALL)
    data.history(KEYS, "SPY", date(2026, 9, 1), as_of, NOW + timedelta(days=1), Adjustment.ALL)
    assert len(fetches.calls) == 2  # a split or dividend tomorrow rewrites every adjusted close


def test_the_calendar_is_cached_for_a_day(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def sessions(trading: Any, start: date, end: date) -> list[data.Session]:
        calls.append(1)
        return SESSIONS

    monkeypatch.setattr(data, "sessions", sessions)
    first = data.cached_sessions(object(), AS_OF, AS_OF, NOW)  # type: ignore[arg-type]
    assert first == data.cached_sessions(object(), AS_OF, AS_OF, NOW + timedelta(hours=23))  # type: ignore[arg-type]
    assert first == SESSIONS and len(calls) == 1
    data.cached_sessions(object(), AS_OF, AS_OF, NOW + timedelta(days=1, seconds=1))  # type: ignore[arg-type]
    assert len(calls) == 2


def test_offline_bars_cannot_reach_alpaca() -> None:
    with pytest.raises(Tripwire):
        data._fetch_closes(KEYS, "SPY", AS_OF, NOW, Adjustment.RAW)
