"""A deterministic read through cache at $TRADERBOT_HOME/cache.sqlite, and the retry rule.

`get_or_fetch` stores the fetcher's result as canonical JSON (sorted keys, ASCII, no NaN) with
its sha256. A row whose body no longer matches its digest, or does not parse, or holds a NaN or
an infinity (which Python's JSON reader would otherwise accept), or was stamped in the future,
is a miss: it is fetched again and overwritten, never served. Writes take
`BEGIN IMMEDIATE`; every statement binds its values. A hit and a miss return the same shape,
because a miss returns the stored text parsed back, not the fetcher's own objects.

`retry_with_backoff` retries only a named transient failure (a timeout, a dropped connection,
HTTP 429 or 5xx), at most 3 attempts, sleeping through a hook tests replace.
"""

import hashlib
import json
import re
import sqlite3
import time
import urllib.error
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from alpaca.common.exceptions import APIError

from traderbot import paths

NAMESPACE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
TRANSIENT_HTTP = frozenset({429, 500, 502, 503, 504})
ATTEMPTS = 3
DELAYS_S = (1.0, 2.0)  # before the second and the third attempt

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    body TEXT NOT NULL,
    digest TEXT NOT NULL CHECK (length(digest) = 64),
    PRIMARY KEY (namespace, key)
);
"""


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def path() -> Path:
    return paths.home() / "cache.sqlite"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(path())
    conn.executescript(SCHEMA)
    return conn


def canonical(value: Any) -> str:
    """Sorted, compact, ASCII JSON; a NaN or an infinity raises ValueError instead of storing."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def is_transient(err: BaseException) -> bool:
    """A failure worth another attempt: a timeout, a dropped connection, HTTP 429 or 5xx."""
    if isinstance(err, urllib.error.HTTPError):
        return err.code in TRANSIENT_HTTP
    if isinstance(err, APIError):
        return err.status_code in TRANSIENT_HTTP
    if isinstance(err, requests.HTTPError):
        return err.response is not None and err.response.status_code in TRANSIENT_HTTP
    return isinstance(
        err,
        (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            requests.ConnectionError,
            requests.Timeout,
        ),
    )


def retry_with_backoff[T](
    fn: Callable[[], T],
    attempts: int = ATTEMPTS,
    delays: Sequence[float] = DELAYS_S,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Call `fn`; on a transient failure sleep and try again, up to `attempts` calls in all.

    Anything else, and the last transient failure, propagates. Only `Exception` is caught, so a
    test tripwire (a BaseException) always gets through on the first call.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    pause = sleep or _sleep
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as err:
            if attempt == attempts - 1 or not is_transient(err):
                raise
            pause(delays[min(attempt, len(delays) - 1)])
    raise AssertionError("unreachable: the loop returns or raises")


@contextmanager
def _immediate(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _no_constant(name: str) -> Any:
    raise ValueError(f"cached JSON holds {name}")


def lookup(conn: sqlite3.Connection, namespace: str, key: str, now: datetime, ttl_s: int) -> Any:
    """The stored value, or None on a miss (absent, expired, future stamped, or corrupt)."""
    row = conn.execute(
        "SELECT fetched_at, body, digest FROM entries WHERE namespace = ? AND key = ?",
        (namespace, key),
    ).fetchone()
    if row is None:
        return None
    fetched_at, body, digest = row
    try:
        age = (now - datetime.fromisoformat(fetched_at)).total_seconds()
    except (TypeError, ValueError):
        return None
    if not 0 <= age <= ttl_s or _digest(body) != digest:
        return None
    try:
        return json.loads(body, parse_constant=_no_constant)
    except ValueError:  # JSONDecodeError included: corrupt, or a NaN or an infinity
        return None


def get_or_fetch(
    namespace: str,
    key: str,
    now: datetime,
    ttl_seconds: int,
    fetcher: Callable[[], Any],
    sleep: Callable[[float], None] | None = None,
) -> Any:
    """The cached value for (namespace, key) if fresh, else `fetcher()` (with retry), stored."""
    if not NAMESPACE_RE.fullmatch(namespace):
        raise ValueError(f"cache namespace must match {NAMESPACE_RE.pattern}, got {namespace!r}")
    if now.tzinfo is None:
        raise ValueError("cache time must be aware")
    if ttl_seconds <= 0:
        raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
    conn = connect()
    try:
        hit = lookup(conn, namespace, key, now, ttl_seconds)
        if hit is not None:
            return hit
        body = canonical(retry_with_backoff(fetcher, sleep=sleep))
        with _immediate(conn):
            conn.execute(
                "INSERT OR REPLACE INTO entries VALUES (?, ?, ?, ?, ?)",
                (namespace, key, now.isoformat(), body, _digest(body)),
            )
        return json.loads(body)
    finally:
        conn.close()
