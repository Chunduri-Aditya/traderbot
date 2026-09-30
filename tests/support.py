"""Shared offline fixtures: a fixed clock, a card and a snapshot that pass every gate rule."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from traderbot import data, gate
from traderbot import proposal as prop

ET = data.ET
AS_OF = date(2026, 9, 29)
NOW = datetime(2026, 9, 29, 18, 0, tzinfo=ET)  # after that session's 16:00 close
NEXT_CLOSE = datetime(2026, 9, 30, 16, 0, tzinfo=ET)
SESSIONS = [
    data.Session(
        day=AS_OF,
        open=datetime(2026, 9, 29, 9, 30, tzinfo=ET),
        close=datetime(2026, 9, 29, 16, 0, tzinfo=ET),
    ),
    data.Session(
        day=date(2026, 9, 30),
        open=datetime(2026, 9, 30, 9, 30, tzinfo=ET),
        close=NEXT_CLOSE,
    ),
]
LIMITS = gate.Limits()


class Tripwire(BaseException):
    """A guarded call ran that the path under test must never reach."""


def trip(*args: Any, **kwargs: Any) -> Any:
    raise Tripwire("a guarded call ran")


def a_card(**changes: Any) -> prop.Proposal:
    card = prop.Proposal(
        symbol="SPY",
        requested_symbol="SPY",
        side="buy",
        rationale="offline test card",
        confidence=0.5,
        qty=1,
        limit_price=Decimal("768.02"),
        order_type="limit",
        time_in_force="day",
        as_of=AS_OF,
        bar_date=AS_OF,
        last_close=Decimal("764.20"),
        desk="single",
        model="claude-sonnet-5-5",
        created_at=NOW,
        expires_at=NEXT_CLOSE,
    )
    return replace(card, **changes)


def a_snapshot(**changes: Any) -> gate.AccountSnapshot:
    snapshot = gate.AccountSnapshot(
        buying_power=Decimal(100000),
        equity=Decimal(100000),
        equity_peak=Decimal(100000),
        position_qty=0,
        position_value=Decimal(0),
        open_order_symbols=frozenset(),
        expected_as_of=AS_OF,
        day_spend_usd=Decimal(0),
        orders_today=0,
        consecutive_losses=0,
        kill_switch=False,
        breaker_latched=False,
    )
    return replace(snapshot, **changes)
