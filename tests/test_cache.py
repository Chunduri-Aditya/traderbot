"""The cache: hits within the ttl, misses on expiry, corruption or a future stamp; retry only
on transient failures; nothing stored that cannot be canonical JSON."""

import sqlite3
import urllib.error
from datetime import timedelta
from email.message import Message
from typing import Any

import pytest
from support import NOW, Tripwire

from traderbot import cache, paths


class Counter:
    def __init__(self, *values: Any) -> None:
        self.values = list(values)
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://data.sec.gov/x", code, "err", Message(), None)


def test_a_hit_inside_the_ttl_does_not_fetch_again() -> None:
    fetch = Counter({"a": 1}, {"a": 2})
    assert cache.get_or_fetch("t", "k", NOW, 60, fetch) == {"a": 1}
    assert cache.get_or_fetch("t", "k", NOW + timedelta(seconds=60), 60, fetch) == {"a": 1}
    assert fetch.calls == 1


def test_an_expired_entry_is_fetched_again() -> None:
    fetch = Counter({"a": 1}, {"a": 2})
    cache.get_or_fetch("t", "k", NOW, 60, fetch)
    assert cache.get_or_fetch("t", "k", NOW + timedelta(seconds=61), 60, fetch) == {"a": 2}
    assert fetch.calls == 2


def test_an_entry_stamped_in_the_future_is_a_miss() -> None:
    fetch = Counter([1], [2])
    cache.get_or_fetch("t", "k", NOW + timedelta(hours=1), 3600, fetch)
    assert cache.get_or_fetch("t", "k", NOW, 3600, fetch) == [2]


def test_a_poisoned_body_is_refetched_not_served() -> None:
    fetch = Counter({"close": "100"}, {"close": "100"})
    cache.get_or_fetch("bars", "SPY", NOW, 60, fetch)
    conn = sqlite3.connect(cache.path())
    with conn:
        conn.execute("UPDATE entries SET body = ? WHERE key = 'SPY'", ('{"close":"1"}',))
    assert cache.get_or_fetch("bars", "SPY", NOW, 60, fetch) == {"close": "100"}
    assert fetch.calls == 2


def test_hit_and_miss_return_the_same_shape() -> None:
    fetch = Counter({"rows": (("2026-09-29", "1.5"),)})
    first = cache.get_or_fetch("t", "k", NOW, 60, fetch)
    assert (
        first == cache.get_or_fetch("t", "k", NOW, 60, fetch) == {"rows": [["2026-09-29", "1.5"]]}
    )


def test_nan_is_never_stored() -> None:
    with pytest.raises(ValueError):
        cache.get_or_fetch("t", "k", NOW, 60, lambda: {"close": float("nan")})
    assert cache.connect().execute("SELECT count(*) FROM entries").fetchone() == (0,)


def test_keys_are_bound_never_spliced() -> None:
    key = "x'); DROP TABLE entries; --"
    assert cache.get_or_fetch("t", key, NOW, 60, lambda: [1]) == [1]
    assert cache.get_or_fetch("t", key, NOW, 60, Counter()) == [1]


@pytest.mark.parametrize("namespace", ["", "Bars", "a-b", "x" * 33])
def test_bad_namespace_ttl_or_naive_time_is_refused(namespace: str) -> None:
    with pytest.raises(ValueError):
        cache.get_or_fetch(namespace, "k", NOW, 60, lambda: [1])
    with pytest.raises(ValueError):
        cache.get_or_fetch("t", "k", NOW, 0, lambda: [1])
    with pytest.raises(ValueError):
        cache.get_or_fetch("t", "k", NOW.replace(tzinfo=None), 60, lambda: [1])


def test_cache_lives_under_traderbot_home_at_0700() -> None:
    cache.get_or_fetch("t", "k", NOW, 60, lambda: [1])
    assert cache.path().parent == paths.home()
    assert paths.home().stat().st_mode & 0o777 == 0o700


def test_retry_recovers_from_transient_failures_with_the_named_delays() -> None:
    slept: list[float] = []
    fetch = Counter(TimeoutError("slow"), http_error(503), "ok")
    assert cache.retry_with_backoff(fetch, sleep=slept.append) == "ok"
    assert fetch.calls == 3 and slept == [1.0, 2.0]


def test_retry_stops_after_three_attempts() -> None:
    slept: list[float] = []
    fetch = Counter(http_error(429), http_error(429), http_error(429), "never")
    with pytest.raises(urllib.error.HTTPError):
        cache.retry_with_backoff(fetch, sleep=slept.append)
    assert fetch.calls == 3 and slept == [1.0, 2.0]


@pytest.mark.parametrize(
    "err", [http_error(404), http_error(403), ValueError("bad json"), KeyError("x")]
)
def test_retry_never_repeats_a_permanent_failure(err: Exception) -> None:
    fetch = Counter(err, "never")
    with pytest.raises(type(err)):
        cache.retry_with_backoff(fetch, sleep=lambda s: None)
    assert fetch.calls == 1


def test_retry_lets_a_tripwire_through_on_the_first_call() -> None:
    fetch = Counter(Tripwire("network"), "never")
    with pytest.raises(Tripwire):
        cache.retry_with_backoff(fetch, sleep=lambda s: None)
    assert fetch.calls == 1


def test_a_failed_fetch_stores_nothing() -> None:
    with pytest.raises(urllib.error.HTTPError):
        cache.get_or_fetch("t", "k", NOW, 60, Counter(http_error(404)), sleep=lambda s: None)
    assert cache.connect().execute("SELECT count(*) FROM entries").fetchone() == (0,)
