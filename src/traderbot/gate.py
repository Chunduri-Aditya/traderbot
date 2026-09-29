"""The deterministic gate: a pure function over (proposal, account snapshot, limits, now).

It returns Allow, or Deny with every reason code that applies. Nothing here reads the network,
the clock, or the ledger; the caller gathers the snapshot and passes `now`.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from traderbot.proposal import Proposal


@dataclass(frozen=True)
class Limits:
    """Aditya's Phase 1 numbers (2026-09-29). Phase 3 moves them to ~/.traderbot/config.toml."""

    max_notional: Decimal = Decimal(1000)
    band: Decimal = Decimal("0.005")
    day_spend_cap_usd: Decimal = Decimal("2.00")


@dataclass(frozen=True)
class AccountSnapshot:
    buying_power: Decimal
    open_order_symbols: frozenset[str]
    expected_as_of: date  # last closed session by the calendar at `now`
    day_spend_usd: Decimal  # model spend on the ET date of `now`, other cards only


@dataclass(frozen=True)
class Allow:
    allowed: bool = True


@dataclass(frozen=True)
class Deny:
    reasons: tuple[str, ...]
    allowed: bool = False


GateResult = Allow | Deny


def check(
    proposal: Proposal, cli_symbol: str, snapshot: AccountSnapshot, limits: Limits, now: datetime
) -> GateResult:
    """`now` is the moment the snapshot was read; no Phase 1 rule reads it (expiry is Phase 2)."""
    reasons: list[str] = []
    notional = proposal.qty * proposal.limit_price
    if proposal.symbol != cli_symbol:
        reasons.append("SYMBOL_MISMATCH")
    if proposal.side != "buy":
        reasons.append("SIDE_NOT_ALLOWED")
    if proposal.bar_date != snapshot.expected_as_of or proposal.as_of != snapshot.expected_as_of:
        reasons.append("STALE_BARS")
    if proposal.qty <= 0 or proposal.limit_price <= 0:
        reasons.append("QTY_ZERO")
    if notional > limits.max_notional:
        reasons.append("NOTIONAL_OVER_MAX")
    if notional > snapshot.buying_power:
        reasons.append("BUYING_POWER")
    if proposal.symbol in snapshot.open_order_symbols:
        reasons.append("OPEN_ORDER_EXISTS")
    if snapshot.day_spend_usd >= limits.day_spend_cap_usd:
        reasons.append("DAY_SPEND_CAP")
    return Deny(tuple(reasons)) if reasons else Allow()
