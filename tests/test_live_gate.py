"""live-check: each reason code has a deny whose input crosses its limit and an allow on the
limit itself (one record sitting on every boundary passes); missing numbers fail closed."""

from dataclasses import replace
from decimal import Decimal

import pytest

from traderbot import live_gate
from traderbot.scoring import ScoreReport

LIMITS = live_gate.LiveLimits()
ON_EVERY_LIMIT = ScoreReport(
    settled=20,
    win_rate=Decimal("0.55"),
    mean_return=Decimal("0.004"),
    mean_alpha_bps=Decimal(30),
    mean_brier=Decimal("0.25"),
    alpha_ci=(Decimal("0.00"), Decimal(60)),
    brier_ci=(Decimal("0.2"), Decimal("0.3")),
    seed=42,
    resamples=1000,
)
BASELINE = replace(ON_EVERY_LIMIT, mean_alpha_bps=Decimal(30))
CLEAN = live_gate.LiveState(kill_switch=False, breaker_latched=False, unconfirmed_orders=0)


def check(
    score: ScoreReport = ON_EVERY_LIMIT,
    state: live_gate.LiveState = CLEAN,
    baseline: ScoreReport | None = BASELINE,
) -> live_gate.LiveCheckVerdict:
    return live_gate.check_live_readiness(score, state, baseline, LIMITS)


def denies(verdict: live_gate.LiveCheckVerdict, code: str) -> bool:
    return not verdict.passed and code in verdict.reasons


def test_allow_at_every_boundary() -> None:
    # 20 settled, a lower CI bound of exactly 0, a Brier of exactly 0.25, alpha equal to the
    # baseline's, nothing latched, no claims: the record sits on every limit and passes.
    assert check() == live_gate.LiveCheckVerdict(passed=True, reasons=())


def test_insufficient_settlements_deny() -> None:
    assert denies(check(replace(ON_EVERY_LIMIT, settled=19)), "INSUFFICIENT_SETTLEMENTS")


def test_negative_alpha_ci_deny() -> None:
    low = replace(ON_EVERY_LIMIT, alpha_ci=(Decimal("-0.01"), Decimal(60)))
    assert denies(check(low), "NEGATIVE_ALPHA_CI")


def test_negative_alpha_ci_deny_without_a_ci() -> None:
    assert denies(check(replace(ON_EVERY_LIMIT, alpha_ci=None)), "NEGATIVE_ALPHA_CI")


def test_brier_over_max_deny() -> None:
    assert denies(check(replace(ON_EVERY_LIMIT, mean_brier=Decimal("0.2501"))), "BRIER_OVER_MAX")


def test_brier_over_max_deny_without_a_brier() -> None:
    assert denies(check(replace(ON_EVERY_LIMIT, mean_brier=None)), "BRIER_OVER_MAX")


def test_no_baseline_deny() -> None:
    assert denies(check(baseline=None), "NO_BASELINE")
    assert denies(check(baseline=replace(BASELINE, mean_alpha_bps=None)), "NO_BASELINE")


def test_no_baseline_deny_on_a_thin_baseline() -> None:
    # The allow sits on the limit: BASELINE has exactly 20 settled (test_allow_at_every_boundary).
    assert denies(check(baseline=replace(BASELINE, settled=19)), "NO_BASELINE")


def test_underperforms_baseline_deny() -> None:
    better = replace(BASELINE, mean_alpha_bps=Decimal("30.01"))
    assert denies(check(baseline=better), "UNDERPERFORMS_BASELINE")
    assert denies(check(replace(ON_EVERY_LIMIT, mean_alpha_bps=None)), "UNDERPERFORMS_BASELINE")


def test_breaker_latched_deny() -> None:
    assert denies(check(state=replace(CLEAN, breaker_latched=True)), "BREAKER_LATCHED")


def test_kill_switch_active_deny() -> None:
    assert denies(check(state=replace(CLEAN, kill_switch=True)), "KILL_SWITCH_ACTIVE")


def test_unconfirmed_orders_deny() -> None:
    assert denies(check(state=replace(CLEAN, unconfirmed_orders=1)), "UNCONFIRMED_ORDERS")


def test_every_reason_is_listed_together_on_an_empty_record() -> None:
    empty = ScoreReport(0, None, None, None, None, None, None, 42, 1000)
    state = live_gate.LiveState(kill_switch=True, breaker_latched=True, unconfirmed_orders=2)
    assert (
        check(empty, state, None).reasons == live_gate.LIVE_REASONS[:4] + live_gate.LIVE_REASONS[5:]
    )


@pytest.mark.parametrize("code", live_gate.LIVE_REASONS)
def test_each_reason_has_a_deny_test(code: str) -> None:
    assert f"test_{code.lower()}_deny" in globals()
