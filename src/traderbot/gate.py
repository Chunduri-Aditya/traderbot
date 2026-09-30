"""The deterministic gate: a pure function over (proposal, account snapshot, limits, now).

It returns Allow, or Deny with every reason code that applies. Nothing here reads the network,
the clock, or the ledger; the caller gathers the snapshot and passes `now`.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from traderbot.proposal import Proposal

# Every reason code the spec names, in its order. scripts/mutate.py keeps its own copy on purpose.
REASONS = (
    "SYMBOL_MISMATCH",
    "SIDE_NOT_ALLOWED",
    "STALE_BARS",
    "QTY_ZERO",
    "NOTIONAL_OVER_MAX",
    "BUYING_POWER",
    "OPEN_ORDER_EXISTS",
    "POSITION_OVER_MAX_PCT",
    "DAILY_ORDER_CAP",
    "DAY_SPEND_CAP",
    "KILL_SWITCH",
    "CARD_EXPIRED",
    "SELL_EXCEEDS_POSITION",
    "CIRCUIT_BREAKER",
)


@dataclass(frozen=True)
class Limits:
    """Aditya's numbers (Phase 1 and 2, 2026-09-29). Phase 3 moves them to config.toml."""

    max_notional: Decimal = Decimal(1000)
    band: Decimal = Decimal("0.005")
    day_spend_cap_usd: Decimal = Decimal("2.00")
    max_position_pct: Decimal = Decimal("0.10")
    daily_order_cap: int = 3
    max_consecutive_losses: int = 3
    max_drawdown_pct: Decimal = Decimal("0.10")


@dataclass(frozen=True)
class AccountSnapshot:
    buying_power: Decimal
    equity: Decimal
    equity_peak: Decimal  # the highest equity traderbot has recorded, this reading included
    position_qty: int  # shares held in the proposal's symbol
    position_value: Decimal  # market value of that position
    open_order_symbols: frozenset[str]
    expected_as_of: date  # last closed session by the calendar at `now`
    day_spend_usd: Decimal  # model spend on the ET date of `now`, settled plus held
    orders_today: int  # orders traderbot placed on the ET date of `now`
    consecutive_losses: int  # latest settled fills that lost money, in a row
    kill_switch: bool
    breaker_latched: bool  # tripped earlier and not reset by Aditya


@dataclass(frozen=True)
class Allow:
    allowed: bool = True


@dataclass(frozen=True)
class Deny:
    reasons: tuple[str, ...]
    allowed: bool = False


GateResult = Allow | Deny


def breaker_condition(snapshot: AccountSnapshot, limits: Limits) -> bool:
    """True when the loss streak or the drawdown from the equity peak reaches its limit.

    A peak at or below zero trips too: no drawdown can be measured from it, so the gate fails
    closed (the ECC circuit breaker halts on an invalid starting value the same way).
    """
    losing = snapshot.consecutive_losses >= limits.max_consecutive_losses
    if snapshot.equity_peak <= 0:
        return True
    drawdown = (snapshot.equity_peak - snapshot.equity) / snapshot.equity_peak
    return losing or drawdown >= limits.max_drawdown_pct


def check(
    proposal: Proposal, cli_symbol: str, snapshot: AccountSnapshot, limits: Limits, now: datetime
) -> GateResult:
    """Every rule runs; a deny lists each code that applies. Buys and sells are the only sides."""
    reasons: list[str] = []
    limit = proposal.limit_price
    # A non finite limit (a NaN or infinite close upstream) sizes nothing: QTY_ZERO, never a crash.
    sizable = limit.is_finite() and limit > 0 and proposal.qty > 0
    notional = proposal.qty * limit if sizable else Decimal(0)
    buying = proposal.side == "buy"
    if proposal.symbol != cli_symbol:
        reasons.append("SYMBOL_MISMATCH")
    if proposal.side not in ("buy", "sell"):
        reasons.append("SIDE_NOT_ALLOWED")
    if proposal.bar_date != snapshot.expected_as_of or proposal.as_of != snapshot.expected_as_of:
        reasons.append("STALE_BARS")
    if not sizable:
        reasons.append("QTY_ZERO")
    if notional > limits.max_notional:
        reasons.append("NOTIONAL_OVER_MAX")
    if buying and notional > snapshot.buying_power:
        reasons.append("BUYING_POWER")
    if proposal.symbol in snapshot.open_order_symbols:
        reasons.append("OPEN_ORDER_EXISTS")
    if buying and snapshot.position_value + notional > limits.max_position_pct * snapshot.equity:
        reasons.append("POSITION_OVER_MAX_PCT")
    if snapshot.orders_today >= limits.daily_order_cap:
        reasons.append("DAILY_ORDER_CAP")
    if snapshot.day_spend_usd > limits.day_spend_cap_usd:
        reasons.append("DAY_SPEND_CAP")
    if snapshot.kill_switch:
        reasons.append("KILL_SWITCH")
    if proposal.expires_at is None or now >= proposal.expires_at:
        reasons.append("CARD_EXPIRED")
    if proposal.side == "sell" and proposal.qty > snapshot.position_qty:
        reasons.append("SELL_EXCEEDS_POSITION")
    if snapshot.breaker_latched or breaker_condition(snapshot, limits):
        reasons.append("CIRCUIT_BREAKER")
    return Deny(tuple(reasons)) if reasons else Allow()
