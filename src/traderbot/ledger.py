"""The ledger: sqlite under TRADERBOT_HOME (default ~/.traderbot). Tests point it at tmp_path.

`cards` is keyed by the sha256 of the proposal's canonical JSON; approve checks the stored text
against its key, so an edited row no longer matches. `orders` maps a card to its Alpaca order,
`decisions` holds the first approve or reject per card (a primary key, so a second one fails),
`spend` holds claude reservations until they settle, and `events` is append only by trigger.
`equity_marks`, `settlements` and `breaker` feed the circuit breaker.
"""

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from traderbot import proposal as prop
from traderbot.gate import Deny, GateResult

HOME_ENV = "TRADERBOT_HOME"
DEFAULT_HOME = "~/.traderbot"
KILL_SWITCH_FILE = "OFF"
PREFIX_RE = re.compile(r"[0-9a-f]{8,64}")
RESERVATION_TIMEOUT_S = 600  # twice the desk's 300 s call timeout
DECISIONS = ("approve", "reject")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    digest TEXT PRIMARY KEY CHECK (length(digest) = 64),
    created_at TEXT NOT NULL,
    spend_day TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    total_cost_usd TEXT NOT NULL,
    model_usage_json TEXT NOT NULL,
    gate_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    digest TEXT PRIMARY KEY REFERENCES cards(digest),
    order_id TEXT NOT NULL,
    status TEXT NOT NULL,
    submitted_day TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    filled_qty TEXT,
    filled_avg_price TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    digest TEXT PRIMARY KEY REFERENCES cards(digest),
    decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    reserved_usd TEXT NOT NULL,
    reserved_at TEXT NOT NULL,
    settled_usd TEXT,
    settled_at TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    digest TEXT,
    details_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equity_marks (
    at TEXT NOT NULL,
    equity TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settlements (
    digest TEXT PRIMARY KEY REFERENCES cards(digest),
    kind TEXT NOT NULL CHECK (kind IN ('fill', 'counterfactual')),
    settled_at TEXT NOT NULL,
    raw_return TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS breaker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tripped_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    reset_at TEXT
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append only'); END;
"""


class SpendCapReached(Exception):
    """A reservation would take the day's model spend past the cap."""


@dataclass(frozen=True)
class Card:
    digest: str
    proposal: prop.Proposal
    proposal_json: str
    total_cost_usd: Decimal
    model_usage: tuple[str, ...]
    allowed: bool
    reasons: tuple[str, ...]
    order_id: str | None
    order_status: str | None
    filled_qty: str | None
    decision: str | None


def home() -> Path:
    root = Path(os.environ.get(HOME_ENV) or DEFAULT_HOME).expanduser()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def path() -> Path:
    return home() / "ledger.sqlite"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(path())
    _migrate_orders(conn)
    conn.executescript(SCHEMA)
    return conn


def _migrate_orders(conn: sqlite3.Connection) -> None:
    """Phase 1's `orders` had `recorded_at`; replace it while it is empty, refuse if it is not."""
    columns = [row[1] for row in conn.execute("PRAGMA table_info(orders)")]
    if not columns or "submitted_day" in columns:
        return
    (count,) = conn.execute("SELECT count(*) FROM orders").fetchone()
    if count:
        raise RuntimeError(f"orders holds {count} Phase 1 rows; migrate them by hand")
    with conn:
        conn.execute("DROP TABLE orders")


def insert_card(
    conn: sqlite3.Connection,
    card: prop.Proposal,
    total_cost_usd: Decimal,
    model_usage: tuple[str, ...],
    gate: GateResult,
) -> str:
    key = prop.digest(card)
    reasons = list(gate.reasons) if isinstance(gate, Deny) else []
    with conn:
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                card.created_at.isoformat(),
                card.created_at.date().isoformat(),
                card.symbol,
                card.side,
                prop.canonical(card),
                str(total_cost_usd),
                json.dumps(list(model_usage)),
                json.dumps({"allowed": gate.allowed, "reasons": reasons}),
            ),
        )
    return key


_SELECT = (
    "SELECT c.digest, c.proposal_json, c.total_cost_usd, c.model_usage_json, c.gate_json,"
    " o.order_id, o.status, o.filled_qty, d.decision FROM cards c"
    " LEFT JOIN orders o ON o.digest = c.digest LEFT JOIN decisions d ON d.digest = c.digest"
)


def _card(row: tuple[Any, ...]) -> Card:
    key, proposal_json, cost, usage, gate_json, order_id, status, filled_qty, decision = row
    gate = json.loads(gate_json)
    return Card(
        digest=key,
        proposal=prop.from_json(proposal_json),
        proposal_json=proposal_json,
        total_cost_usd=Decimal(cost),
        model_usage=tuple(json.loads(usage)),
        allowed=bool(gate["allowed"]),
        reasons=tuple(gate["reasons"]),
        order_id=order_id,
        order_status=status,
        filled_qty=filled_qty,
        decision=decision,
    )


def find(conn: sqlite3.Connection, prefix: str) -> Card:
    """The one card whose digest starts with `prefix` (8 to 64 lowercase hex characters)."""
    if not PREFIX_RE.fullmatch(prefix):
        raise ValueError("a card prefix is 8 to 64 lowercase hex characters")
    rows = conn.execute(f"{_SELECT} WHERE substr(c.digest, 1, ?) = ?", (len(prefix), prefix))
    found = rows.fetchall()
    if len(found) != 1:
        raise LookupError(f"{len(found)} cards match prefix {prefix}, want exactly 1")
    return _card(found[0])


def recent(conn: sqlite3.Connection, limit: int = 20) -> list[Card]:
    rows = conn.execute(f"{_SELECT} ORDER BY c.created_at DESC LIMIT ?", (limit,))
    return [_card(row) for row in rows.fetchall()]


def record_order(
    conn: sqlite3.Connection,
    key: str,
    order_id: str,
    status: str,
    now: datetime,
    filled_qty: str | None = None,
    filled_avg_price: str | None = None,
) -> None:
    """Insert the order once (its submitted day never changes), then keep its state current."""
    with conn:
        conn.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(digest) DO UPDATE SET"
            " order_id = excluded.order_id, status = excluded.status,"
            " updated_at = excluded.updated_at,"
            " filled_qty = coalesce(excluded.filled_qty, orders.filled_qty),"
            " filled_avg_price = coalesce(excluded.filled_avg_price, orders.filled_avg_price)",
            (
                key,
                order_id,
                status,
                now.date().isoformat(),
                now.isoformat(),
                filled_qty,
                filled_avg_price,
            ),
        )


def orders_today(conn: sqlite3.Connection, day: date) -> int:
    (count,) = conn.execute(
        "SELECT count(*) FROM orders WHERE submitted_day = ?", (day.isoformat(),)
    ).fetchone()
    return int(count)


def open_orders(conn: sqlite3.Connection) -> list[str]:
    """Digests whose recorded order may still change (not filled, cancelled, expired or rejected)."""
    rows = conn.execute(
        "SELECT digest FROM orders WHERE status NOT IN"
        " ('filled', 'canceled', 'expired', 'rejected', 'done_for_day', 'replaced')"
    )
    return [digest for (digest,) in rows]


def record_decision(conn: sqlite3.Connection, key: str, decision: str, now: datetime) -> str:
    """Store `decision` unless one exists; return the decision that stands (the first wins)."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    with _immediate(conn):
        conn.execute(
            "INSERT OR IGNORE INTO decisions VALUES (?, ?, ?)", (key, decision, now.isoformat())
        )
        (standing,) = conn.execute(
            "SELECT decision FROM decisions WHERE digest = ?", (key,)
        ).fetchone()
    return str(standing)


@contextmanager
def _immediate(conn: sqlite3.Connection) -> Iterator[None]:
    """One write transaction taken up front, so two processes cannot interleave inside it."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _expire(conn: sqlite3.Connection, now: datetime) -> None:
    """Charge in full every reservation older than the timeout: its call never settled."""
    rows = conn.execute("SELECT id, reserved_at FROM spend WHERE settled_usd IS NULL")
    for rid, reserved_at in rows.fetchall():
        age = (now - datetime.fromisoformat(reserved_at)).total_seconds()
        if age > RESERVATION_TIMEOUT_S:
            conn.execute(
                "UPDATE spend SET settled_usd = reserved_usd, settled_at = ?, note = 'expired'"
                " WHERE id = ?",
                (now.isoformat(), rid),
            )


def _spent(conn: sqlite3.Connection, day: date) -> Decimal:
    rows = conn.execute(
        "SELECT coalesce(settled_usd, reserved_usd) FROM spend WHERE day = ?", (day.isoformat(),)
    )
    return sum((Decimal(usd) for (usd,) in rows.fetchall()), Decimal(0))


def reserve(conn: sqlite3.Connection, usd: Decimal, cap: Decimal, now: datetime) -> int:
    """Hold `usd` against today's cap in one transaction; raise SpendCapReached past it."""
    with _immediate(conn):
        _expire(conn, now)
        spent = _spent(conn, now.date())
        if spent + usd > cap:
            raise SpendCapReached(
                f"day model spend ${spent} plus a ${usd} call would pass the ${cap} cap"
            )
        cursor = conn.execute(
            "INSERT INTO spend (day, reserved_usd, reserved_at) VALUES (?, ?, ?)",
            (now.date().isoformat(), str(usd), now.isoformat()),
        )
    rid = cursor.lastrowid
    if rid is None:
        raise RuntimeError("the spend reservation has no row id")
    return rid


def settle(conn: sqlite3.Connection, rid: int, actual: Decimal | None, now: datetime) -> None:
    """Charge the actual cost, or the full reservation when the cost is missing or invalid.

    A reservation already charged in full after its timeout stays charged in full.
    """
    valid = actual is not None and actual.is_finite() and actual >= 0
    with _immediate(conn):
        row = conn.execute(
            "SELECT reserved_usd, settled_usd FROM spend WHERE id = ?", (rid,)
        ).fetchone()
        if row is None:
            raise LookupError(f"no spend reservation {rid}")
        reserved, settled = row
        if settled is not None:
            return
        charge = actual if valid and actual is not None else Decimal(reserved)
        conn.execute(
            "UPDATE spend SET settled_usd = ?, settled_at = ?, note = ? WHERE id = ?",
            (str(charge), now.isoformat(), "settled" if valid else "charged in full", rid),
        )


def day_spend(conn: sqlite3.Connection, day: date, now: datetime) -> Decimal:
    """Settled plus held spend for `day`, after charging reservations older than the timeout."""
    with _immediate(conn):
        _expire(conn, now)
        return _spent(conn, day)


REDACTED = "[redacted]"
SECRET_WORDS = frozenset(
    {
        "key",
        "apikey",
        "secret",
        "token",
        "password",
        "passwd",
        "authorization",
        "credential",
        "credentials",
    }
)


def _names_a_secret(key: str) -> bool:
    words = re.split(r"[_\-\s]+", re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower())
    return any(word in SECRET_WORDS for word in words)


def redact(value: Any) -> Any:
    """Replace every value under a key that names a credential, at any depth."""
    if isinstance(value, dict):
        return {
            k: REDACTED if isinstance(k, str) and _names_a_secret(k) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def log_event(
    conn: sqlite3.Connection,
    kind: str,
    now: datetime,
    digest: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    text = json.dumps(redact(details or {}), sort_keys=True, default=str, ensure_ascii=True)
    with conn:
        conn.execute(
            "INSERT INTO events (at, kind, digest, details_json) VALUES (?, ?, ?, ?)",
            (now.isoformat(), kind, digest, text),
        )


def mark_equity(conn: sqlite3.Connection, equity: Decimal, now: datetime) -> Decimal:
    """Record this equity reading and return the peak across every reading, this one included."""
    with conn:
        conn.execute("INSERT INTO equity_marks VALUES (?, ?)", (now.isoformat(), str(equity)))
    rows = conn.execute("SELECT equity FROM equity_marks").fetchall()
    return max(Decimal(value) for (value,) in rows)


def consecutive_losses(conn: sqlite3.Connection) -> int:
    """Latest settled fills with a negative raw return, in a row (Phase 4 writes settlements)."""
    rows = conn.execute("SELECT settled_at, raw_return FROM settlements WHERE kind = 'fill'")
    newest_first = sorted(rows.fetchall(), key=lambda r: datetime.fromisoformat(r[0]), reverse=True)
    streak = 0
    for _, raw_return in newest_first:
        if Decimal(raw_return) >= 0:
            break
        streak += 1
    return streak


def breaker_latched(conn: sqlite3.Connection) -> bool:
    (count,) = conn.execute("SELECT count(*) FROM breaker WHERE reset_at IS NULL").fetchone()
    return bool(count)


def latch_breaker(conn: sqlite3.Connection, reason: str, now: datetime) -> None:
    with _immediate(conn):
        (count,) = conn.execute("SELECT count(*) FROM breaker WHERE reset_at IS NULL").fetchone()
        if not count:
            conn.execute(
                "INSERT INTO breaker (tripped_at, reason) VALUES (?, ?)", (now.isoformat(), reason)
            )


def reset_breaker(conn: sqlite3.Connection, now: datetime) -> int:
    """Clear every open trip; return how many were open."""
    with conn:
        cursor = conn.execute(
            "UPDATE breaker SET reset_at = ? WHERE reset_at IS NULL", (now.isoformat(),)
        )
    return cursor.rowcount


def kill_switch_on() -> bool:
    return (home() / KILL_SWITCH_FILE).exists()


def set_kill_switch(on: bool) -> None:
    switch = home() / KILL_SWITCH_FILE
    if on:
        switch.touch(mode=0o600, exist_ok=True)
    else:
        switch.unlink(missing_ok=True)
