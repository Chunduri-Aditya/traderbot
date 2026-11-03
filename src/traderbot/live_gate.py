"""The live readiness check: a pure verdict over the desk's record, the ledger state and a
baseline. No I/O, no clock. Passing it builds nothing: no live code path exists, and going live
still needs a separate plan Aditya approves (PRD non goals).

Every rule runs and a FAIL lists each code that applies. A missing number fails its rule (fail
closed): no CI is NEGATIVE_ALPHA_CI, no Brier is BRIER_OVER_MAX, no mean alpha underperforms.
"""

from dataclasses import dataclass
from decimal import Decimal

from traderbot.scoring import ScoreReport

LIVE_REASONS = (
    "INSUFFICIENT_SETTLEMENTS",
    "NEGATIVE_ALPHA_CI",
    "BRIER_OVER_MAX",
    "NO_BASELINE",
    "UNDERPERFORMS_BASELINE",
    "BREAKER_LATCHED",
    "KILL_SWITCH_ACTIVE",
    "UNCONFIRMED_ORDERS",
)


@dataclass(frozen=True)
class LiveLimits:
    min_settlements: int = 20  # fewer fails
    max_brier: Decimal = Decimal("0.25")  # above fails; 0.25 is a coin flip's score
    min_alpha_ci_low_bps: Decimal = Decimal(0)  # a lower 95% bound below it fails


@dataclass(frozen=True)
class LiveState:
    kill_switch: bool
    breaker_latched: bool
    unconfirmed_orders: int  # order claims with no Alpaca order id recorded


@dataclass(frozen=True)
class LiveCheckVerdict:
    passed: bool
    reasons: tuple[str, ...]


def check_live_readiness(
    score: ScoreReport,
    state: LiveState,
    baseline: ScoreReport | None,
    limits: LiveLimits,
) -> LiveCheckVerdict:
    reasons: list[str] = []
    if score.settled < limits.min_settlements:
        reasons.append("INSUFFICIENT_SETTLEMENTS")
    if score.alpha_ci is None or score.alpha_ci[0] < limits.min_alpha_ci_low_bps:
        reasons.append("NEGATIVE_ALPHA_CI")
    if score.mean_brier is None or score.mean_brier > limits.max_brier:
        reasons.append("BRIER_OVER_MAX")
    if baseline is None or baseline.mean_alpha_bps is None:
        reasons.append("NO_BASELINE")
    elif score.mean_alpha_bps is None or score.mean_alpha_bps < baseline.mean_alpha_bps:
        reasons.append("UNDERPERFORMS_BASELINE")
    if state.breaker_latched:
        reasons.append("BREAKER_LATCHED")
    if state.kill_switch:
        reasons.append("KILL_SWITCH_ACTIVE")
    if state.unconfirmed_orders != 0:
        reasons.append("UNCONFIRMED_ORDERS")
    return LiveCheckVerdict(passed=not reasons, reasons=tuple(reasons))
