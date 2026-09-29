"""The ledger: sqlite under TRADERBOT_HOME (default ~/.traderbot). Tests point it at tmp_path.

`cards` is keyed by the sha256 of the proposal's canonical JSON; approve recomputes it from the
stored JSON, so an edited row no longer matches its key. `orders` maps a card to the Alpaca
order placed for it.
"""

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from traderbot import proposal as prop
from traderbot.gate import Deny, GateResult

HOME_ENV = "TRADERBOT_HOME"
DEFAULT_HOME = "~/.traderbot"
PREFIX_RE = re.compile(r"[0-9a-f]{8,64}")

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
    recorded_at TEXT NOT NULL
);
"""


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


def home() -> Path:
    root = Path(os.environ.get(HOME_ENV) or DEFAULT_HOME).expanduser()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def path() -> Path:
    return home() / "ledger.sqlite"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(path())
    conn.executescript(SCHEMA)
    return conn


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


def day_spend(conn: sqlite3.Connection, day: date, exclude: str | None = None) -> Decimal:
    rows = conn.execute(
        "SELECT total_cost_usd FROM cards WHERE spend_day = ? AND digest IS NOT ?",
        (day.isoformat(), exclude),
    ).fetchall()
    return sum((Decimal(cost) for (cost,) in rows), Decimal(0))


_SELECT = (
    "SELECT c.digest, c.proposal_json, c.total_cost_usd, c.model_usage_json, c.gate_json,"
    " o.order_id, o.status FROM cards c LEFT JOIN orders o ON o.digest = c.digest"
)


def _card(row: tuple[str, str, str, str, str, str | None, str | None]) -> Card:
    key, proposal_json, cost, usage, gate_json, order_id, status = row
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
    conn: sqlite3.Connection, key: str, order_id: str, status: str, now: datetime
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?) ON CONFLICT(digest) DO UPDATE SET"
            " order_id = excluded.order_id, status = excluded.status,"
            " recorded_at = excluded.recorded_at",
            (key, order_id, status, now.isoformat()),
        )
