"""Break each gate rule in turn and require its named test to fail.

Two targets: the order gate (`gate.py`, against tests/test_gate.py) and the live readiness gate
(`live_gate.py`, against tests/test_live_gate.py). Rows are written out here from the spec's
reason codes, never derived from the code, so a rule the code never wrote shows up as an ERROR
row instead of vanishing. Each row asserts its text occurs exactly once, applies the change,
confirms it landed on disk, runs its target's test file, and counts as RED only when pytest
exits 1 with the row's named test among the failures. Exit 2 or 5 is an ERROR row. Each target
is restored by copy from the scratch directory after every row and at exit, and must match HEAD
afterwards (`git diff --quiet`).

Usage: uv run python scripts/mutate.py --scratch <dir>
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE_REL = "src/traderbot/gate.py"
LIVE_REL = "src/traderbot/live_gate.py"


def off(code: str) -> tuple[str, str]:
    return f'reasons.append("{code}")', "pass"


# (row name, text in gate.py, replacement, the test that must fail)
ROWS: list[tuple[str, str, str, str]] = [
    ("SYMBOL_MISMATCH", *off("SYMBOL_MISMATCH"), "test_symbol_mismatch_deny"),
    ("SIDE_NOT_ALLOWED", *off("SIDE_NOT_ALLOWED"), "test_side_not_allowed_deny_hold_and_review"),
    ("STALE_BARS", *off("STALE_BARS"), "test_stale_bars_deny"),
    ("QTY_ZERO", *off("QTY_ZERO"), "test_qty_zero_deny_on_zero_qty_and_zero_limit"),
    ("NOTIONAL_OVER_MAX", *off("NOTIONAL_OVER_MAX"), "test_notional_over_max_deny"),
    ("BUYING_POWER", *off("BUYING_POWER"), "test_buying_power_deny"),
    ("OPEN_ORDER_EXISTS", *off("OPEN_ORDER_EXISTS"), "test_open_order_exists_deny"),
    ("POSITION_OVER_MAX_PCT", *off("POSITION_OVER_MAX_PCT"), "test_position_over_max_pct_deny"),
    ("DAILY_ORDER_CAP", *off("DAILY_ORDER_CAP"), "test_daily_order_cap_deny"),
    ("DAY_SPEND_CAP", *off("DAY_SPEND_CAP"), "test_day_spend_cap_deny"),
    ("KILL_SWITCH", *off("KILL_SWITCH"), "test_kill_switch_deny"),
    (
        "CARD_EXPIRED",
        *off("CARD_EXPIRED"),
        "test_card_expired_deny_at_the_close_and_without_an_expiry",
    ),
    ("SELL_EXCEEDS_POSITION", *off("SELL_EXCEEDS_POSITION"), "test_sell_exceeds_position_deny"),
    (
        "CIRCUIT_BREAKER",
        *off("CIRCUIT_BREAKER"),
        "test_circuit_breaker_deny_on_loss_streak_drawdown_and_latch",
    ),
    (
        "breaker: loss streak ignored",
        "losing = snapshot.consecutive_losses >= limits.max_consecutive_losses",
        "losing = False",
        "test_circuit_breaker_deny_on_loss_streak_drawdown_and_latch",
    ),
    (
        "breaker: drawdown ignored",
        "return losing or drawdown >= limits.max_drawdown_pct",
        "return losing",
        "test_circuit_breaker_deny_on_loss_streak_drawdown_and_latch",
    ),
    (
        "breaker: latch ignored",
        "if snapshot.breaker_latched or breaker_condition(snapshot, limits):",
        "if breaker_condition(snapshot, limits):",
        "test_circuit_breaker_deny_on_loss_streak_drawdown_and_latch",
    ),
    (
        "boundary: drawdown must reach, not pass, 10%",
        "return losing or drawdown >= limits.max_drawdown_pct",
        "return losing or drawdown > limits.max_drawdown_pct",
        "test_circuit_breaker_deny_on_loss_streak_drawdown_and_latch",
    ),
    (
        "boundary: notional at exactly the max",
        "if notional > limits.max_notional:",
        "if notional >= limits.max_notional:",
        "test_notional_over_max_allow_at_exactly_the_max",
    ),
    (
        "boundary: position at exactly 10%",
        "notional > limits.max_position_pct * snapshot.equity:",
        "notional >= limits.max_position_pct * snapshot.equity:",
        "test_position_over_max_pct_allow_at_exactly_the_max",
    ),
    (
        "boundary: spend at exactly the cap",
        "if snapshot.day_spend_usd > limits.day_spend_cap_usd:",
        "if snapshot.day_spend_usd >= limits.day_spend_cap_usd:",
        "test_day_spend_cap_allow_at_exactly_the_cap",
    ),
    (
        "boundary: the third order of the day is the last",
        "if snapshot.orders_today >= limits.daily_order_cap:",
        "if snapshot.orders_today > limits.daily_order_cap:",
        "test_daily_order_cap_deny",
    ),
    (
        "boundary: a card expires at the close itself",
        "now >= proposal.expires_at",
        "now > proposal.expires_at",
        "test_card_expired_deny_at_the_close_and_without_an_expiry",
    ),
    (
        "boundary: selling the whole position",
        'proposal.side == "sell" and proposal.qty > snapshot.position_qty',
        'proposal.side == "sell" and proposal.qty >= snapshot.position_qty',
        "test_sell_exceeds_position_allow_selling_the_whole_position",
    ),
    (
        "boundary: a non finite limit sizes nothing",
        "sizable = limit.is_finite() and limit > 0 and proposal.qty > 0",
        "sizable = limit > 0 and proposal.qty > 0",
        "test_qty_zero_deny_on_a_non_finite_limit",
    ),
    (
        "scope: buying power binds a sell",
        "if buying and notional > snapshot.buying_power:",
        "if notional > snapshot.buying_power:",
        "test_buying_power_does_not_bind_a_sell",
    ),
    (
        "scope: the position cap blocks a sell",
        "if buying and snapshot.position_value + notional",
        "if snapshot.position_value + notional",
        "test_position_cap_does_not_block_a_sell",
    ),
]


def live_off(code: str) -> tuple[str, str]:
    return f'reasons.append("{code}")', "pass"


LIVE_ROWS: list[tuple[str, str, str, str]] = [
    (
        "INSUFFICIENT_SETTLEMENTS",
        *live_off("INSUFFICIENT_SETTLEMENTS"),
        "test_insufficient_settlements_deny",
    ),
    ("NEGATIVE_ALPHA_CI", *live_off("NEGATIVE_ALPHA_CI"), "test_negative_alpha_ci_deny"),
    ("BRIER_OVER_MAX", *live_off("BRIER_OVER_MAX"), "test_brier_over_max_deny"),
    ("NO_BASELINE", *live_off("NO_BASELINE"), "test_no_baseline_deny"),
    (
        "UNDERPERFORMS_BASELINE",
        *live_off("UNDERPERFORMS_BASELINE"),
        "test_underperforms_baseline_deny",
    ),
    ("BREAKER_LATCHED", *live_off("BREAKER_LATCHED"), "test_breaker_latched_deny"),
    ("KILL_SWITCH_ACTIVE", *live_off("KILL_SWITCH_ACTIVE"), "test_kill_switch_active_deny"),
    ("UNCONFIRMED_ORDERS", *live_off("UNCONFIRMED_ORDERS"), "test_unconfirmed_orders_deny"),
    (
        "live boundary: 20 settlements are enough",
        "if score.settled < limits.min_settlements:",
        "if score.settled <= limits.min_settlements:",
        "test_allow_at_every_boundary",
    ),
    (
        "live boundary: a lower CI bound of exactly 0 passes",
        "score.alpha_ci[0] < limits.min_alpha_ci_low_bps",
        "score.alpha_ci[0] <= limits.min_alpha_ci_low_bps",
        "test_allow_at_every_boundary",
    ),
    (
        "live boundary: a Brier of exactly 0.25 passes",
        "score.mean_brier > limits.max_brier",
        "score.mean_brier >= limits.max_brier",
        "test_allow_at_every_boundary",
    ),
    (
        "live boundary: alpha equal to the baseline passes",
        "score.mean_alpha_bps < baseline.mean_alpha_bps",
        "score.mean_alpha_bps <= baseline.mean_alpha_bps",
        "test_allow_at_every_boundary",
    ),
    (
        "live: a baseline thinner than the desk's minimum is no baseline",
        "        or baseline.settled < limits.min_settlements\n",
        "",
        "test_no_baseline_deny_on_a_thin_baseline",
    ),
    (
        "live fail closed: no CI is a deny",
        "if score.alpha_ci is None or score.alpha_ci[0]",
        "if score.alpha_ci is not None and score.alpha_ci[0]",
        "test_negative_alpha_ci_deny_without_a_ci",
    ),
    (
        "live fail closed: no Brier is a deny",
        "if score.mean_brier is None or score.mean_brier",
        "if score.mean_brier is not None and score.mean_brier",
        "test_brier_over_max_deny_without_a_brier",
    ),
]

# (target file, its test file, its rows)
TARGETS: list[tuple[str, str, list[tuple[str, str, str, str]]]] = [
    (GATE_REL, "tests/test_gate.py", ROWS),
    (LIVE_REL, "tests/test_live_gate.py", LIVE_ROWS),
]


def run_row(
    target: Path,
    test_file: str,
    original: str,
    old: str,
    new: str,
    test: str,
    pycache: Path,
) -> tuple[str, str]:
    count = original.count(old)
    if count != 1:
        return "ERROR", f"rule text found {count} times, want 1"
    mutated = original.replace(old, new)
    target.write_text(mutated)
    if target.read_text() != mutated or mutated == original:
        return "ERROR", "the mutation did not land on disk"
    proc = subprocess.run(
        ["uv", "run", "pytest", test_file, "-q", "-rf", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        # A fresh bytecode cache per row: two mutations of equal size written in the same second
        # otherwise share one stale .pyc, and the second row silently tests the first (MISS).
        # COLUMNS keeps pytest from cutting long test names off its summary lines.
        env={**os.environ, "COLUMNS": "10000", "PYTHONPYCACHEPREFIX": str(pycache)},
    )
    named = re.search(
        rf"^FAILED {re.escape(test_file)}::{re.escape(test)}( |$)", proc.stdout, re.MULTILINE
    )
    if proc.returncode == 1 and named:
        return "RED", f"{test} failed"
    if proc.returncode == 0:
        return "GREEN", "every test passed with the rule broken"
    if proc.returncode == 1:
        return "MISS", f"red, but not {test}"
    return "ERROR", f"pytest exit {proc.returncode}"


def _clean(rel: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(ROOT), "diff", "--quiet", "--", rel], check=False
        ).returncode
        == 0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scratch", required=True, help="where each target is copied first")
    args = parser.parse_args()
    for rel, _, _ in TARGETS:
        if not _clean(rel):
            print(f"{rel} differs from HEAD; commit it before mutating", file=sys.stderr)
            return 2
    scratch = Path(args.scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, str, str]] = []
    index = 0
    for rel, test_file, rows in TARGETS:
        target = ROOT / rel
        backup = scratch / f"{target.name}.orig"
        shutil.copyfile(target, backup)
        original = backup.read_text()
        try:
            for name, old, new, test in rows:
                verdict, note = run_row(
                    target, test_file, original, old, new, test, scratch / f"pycache-{index}"
                )
                index += 1
                shutil.copyfile(backup, target)
                results.append((verdict, name, note))
                print(f"{verdict:5}  {name}: {note}", flush=True)
        finally:
            shutil.copyfile(backup, target)
    clean = all(_clean(rel) for rel, _, _ in TARGETS)
    red = sum(verdict == "RED" for verdict, _, _ in results)
    print(f"rows={len(results)} red={red} not_red={len(results) - red}")
    print(f"gate_restored={'clean' if clean else 'DIRTY'}")
    return 0 if red == len(results) and clean else 1


if __name__ == "__main__":
    sys.exit(main())
