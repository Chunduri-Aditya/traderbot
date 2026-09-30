"""One deny and one allow test per reason code, each input crossing the limit by the smallest step.

Expected values come from the spec and Aditya's numbers (2026-09-29): $1,000 max notional, $2.00
day model spend, 10% max position of equity, 3 orders a day, 3 settled losses in a row, 10%
drawdown from the equity peak. A deny test asserts its code is the only reason, so the base card
and snapshot pass every other rule.
"""

from datetime import date, timedelta
from decimal import Decimal

from support import LIMITS, NEXT_CLOSE, NOW, a_card, a_snapshot

from traderbot import gate


def reasons(result: gate.GateResult) -> tuple[str, ...]:
    return result.reasons if isinstance(result, gate.Deny) else ()


def test_base_card_and_snapshot_pass_every_rule() -> None:
    assert gate.check(a_card(), "SPY", a_snapshot(), LIMITS, NOW) == gate.Allow()


def test_every_spec_reason_code_is_known() -> None:
    assert gate.REASONS == (
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


def test_symbol_mismatch_deny() -> None:
    result = gate.check(a_card(symbol="QQQ"), "SPY", a_snapshot(), LIMITS, NOW)
    assert reasons(result) == ("SYMBOL_MISMATCH",)


def test_symbol_mismatch_allow() -> None:
    assert gate.check(a_card(symbol="SPY"), "SPY", a_snapshot(), LIMITS, NOW).allowed


def test_side_not_allowed_deny_hold_and_review() -> None:
    for side in ("hold", "review"):
        result = gate.check(a_card(side=side), "SPY", a_snapshot(), LIMITS, NOW)
        assert reasons(result) == ("SIDE_NOT_ALLOWED",), side


def test_side_not_allowed_allow_buy_and_sell() -> None:
    assert gate.check(a_card(side="buy"), "SPY", a_snapshot(), LIMITS, NOW).allowed
    sell = a_card(side="sell", limit_price=Decimal("760.38"))
    held = a_snapshot(position_qty=1, position_value=Decimal("764.20"))
    assert gate.check(sell, "SPY", held, LIMITS, NOW).allowed


def test_stale_bars_deny() -> None:
    card = a_card(bar_date=date(2026, 9, 28))
    assert reasons(gate.check(card, "SPY", a_snapshot(), LIMITS, NOW)) == ("STALE_BARS",)


def test_stale_bars_allow() -> None:
    card = a_card(bar_date=date(2026, 9, 29), as_of=date(2026, 9, 29))
    assert gate.check(card, "SPY", a_snapshot(), LIMITS, NOW).allowed


def test_qty_zero_deny_on_zero_qty_and_zero_limit() -> None:
    for card in (a_card(qty=0), a_card(limit_price=Decimal(0))):
        assert reasons(gate.check(card, "SPY", a_snapshot(), LIMITS, NOW)) == ("QTY_ZERO",)


def test_qty_zero_allow() -> None:
    assert gate.check(a_card(qty=1), "SPY", a_snapshot(), LIMITS, NOW).allowed


def test_notional_over_max_deny() -> None:
    card = a_card(qty=1, limit_price=Decimal("1000.01"))
    assert reasons(gate.check(card, "SPY", a_snapshot(), LIMITS, NOW)) == ("NOTIONAL_OVER_MAX",)


def test_notional_over_max_allow_at_exactly_the_max() -> None:
    card = a_card(qty=1, limit_price=Decimal("1000.00"))
    assert gate.check(card, "SPY", a_snapshot(), LIMITS, NOW).allowed


def test_buying_power_deny() -> None:
    snapshot = a_snapshot(buying_power=Decimal("768.01"))
    assert reasons(gate.check(a_card(), "SPY", snapshot, LIMITS, NOW)) == ("BUYING_POWER",)


def test_buying_power_allow_at_exactly_the_notional() -> None:
    snapshot = a_snapshot(buying_power=Decimal("768.02"))
    assert gate.check(a_card(), "SPY", snapshot, LIMITS, NOW).allowed


def test_open_order_exists_deny() -> None:
    snapshot = a_snapshot(open_order_symbols=frozenset({"SPY"}))
    assert reasons(gate.check(a_card(), "SPY", snapshot, LIMITS, NOW)) == ("OPEN_ORDER_EXISTS",)


def test_open_order_exists_allow_for_another_symbol() -> None:
    snapshot = a_snapshot(open_order_symbols=frozenset({"QQQ"}))
    assert gate.check(a_card(), "SPY", snapshot, LIMITS, NOW).allowed


def test_position_over_max_pct_deny() -> None:
    # 9232.00 held + 768.02 ordered = 10000.02, over 10% of 100000 equity.
    snapshot = a_snapshot(position_qty=12, position_value=Decimal("9232.00"))
    result = gate.check(a_card(), "SPY", snapshot, LIMITS, NOW)
    assert reasons(result) == ("POSITION_OVER_MAX_PCT",)


def test_position_over_max_pct_allow_at_exactly_the_max() -> None:
    # 9231.98 held + 768.02 ordered = 10000.00, exactly 10% of equity.
    snapshot = a_snapshot(position_qty=12, position_value=Decimal("9231.98"))
    assert gate.check(a_card(), "SPY", snapshot, LIMITS, NOW).allowed


def test_daily_order_cap_deny() -> None:
    snapshot = a_snapshot(orders_today=3)
    assert reasons(gate.check(a_card(), "SPY", snapshot, LIMITS, NOW)) == ("DAILY_ORDER_CAP",)


def test_daily_order_cap_allow() -> None:
    assert gate.check(a_card(), "SPY", a_snapshot(orders_today=2), LIMITS, NOW).allowed


def test_day_spend_cap_deny() -> None:
    snapshot = a_snapshot(day_spend_usd=Decimal("2.01"))
    assert reasons(gate.check(a_card(), "SPY", snapshot, LIMITS, NOW)) == ("DAY_SPEND_CAP",)


def test_day_spend_cap_allow_at_exactly_the_cap() -> None:
    snapshot = a_snapshot(day_spend_usd=Decimal("2.00"))
    assert gate.check(a_card(), "SPY", snapshot, LIMITS, NOW).allowed


def test_kill_switch_deny() -> None:
    snapshot = a_snapshot(kill_switch=True)
    assert reasons(gate.check(a_card(), "SPY", snapshot, LIMITS, NOW)) == ("KILL_SWITCH",)


def test_kill_switch_allow() -> None:
    assert gate.check(a_card(), "SPY", a_snapshot(kill_switch=False), LIMITS, NOW).allowed


def test_card_expired_deny_at_the_close_and_without_an_expiry() -> None:
    for card, now in ((a_card(), NEXT_CLOSE), (a_card(expires_at=None), NOW)):
        assert reasons(gate.check(card, "SPY", a_snapshot(), LIMITS, now)) == ("CARD_EXPIRED",)


def test_card_expired_allow_one_second_before_the_close() -> None:
    now = NEXT_CLOSE - timedelta(seconds=1)
    assert gate.check(a_card(), "SPY", a_snapshot(), LIMITS, now).allowed


def test_sell_exceeds_position_deny() -> None:
    sell = a_card(side="sell", qty=2, limit_price=Decimal("380.00"))
    held = a_snapshot(position_qty=1, position_value=Decimal("764.20"))
    result = gate.check(sell, "SPY", held, LIMITS, NOW)
    assert reasons(result) == ("SELL_EXCEEDS_POSITION",)


def test_sell_exceeds_position_allow_selling_the_whole_position() -> None:
    sell = a_card(side="sell", qty=1, limit_price=Decimal("760.38"))
    held = a_snapshot(position_qty=1, position_value=Decimal("764.20"))
    assert gate.check(sell, "SPY", held, LIMITS, NOW).allowed


def test_circuit_breaker_deny_on_loss_streak_drawdown_and_latch() -> None:
    for snapshot in (
        a_snapshot(consecutive_losses=3),
        a_snapshot(equity=Decimal(90000), equity_peak=Decimal(100000)),
        a_snapshot(breaker_latched=True),
    ):
        result = gate.check(a_card(), "SPY", snapshot, LIMITS, NOW)
        assert reasons(result) == ("CIRCUIT_BREAKER",), snapshot


def test_circuit_breaker_allow_just_inside_both_limits() -> None:
    snapshot = a_snapshot(
        consecutive_losses=2, equity=Decimal("90000.01"), equity_peak=Decimal(100000)
    )
    assert gate.check(a_card(), "SPY", snapshot, LIMITS, NOW).allowed


def test_breaker_condition_reads_both_limits() -> None:
    assert gate.breaker_condition(a_snapshot(consecutive_losses=3), LIMITS)
    assert not gate.breaker_condition(a_snapshot(consecutive_losses=2), LIMITS)
    drawn = a_snapshot(equity=Decimal(90000), equity_peak=Decimal(100000))
    assert gate.breaker_condition(drawn, LIMITS)
    assert not gate.breaker_condition(a_snapshot(), LIMITS)


def test_breaker_condition_on_a_zero_peak_trips() -> None:
    zero = a_snapshot(equity=Decimal(0), equity_peak=Decimal(0))
    assert gate.breaker_condition(zero, LIMITS)


def test_buying_power_does_not_bind_a_sell() -> None:
    sell = a_card(side="sell", limit_price=Decimal("760.38"))
    held = a_snapshot(position_qty=1, position_value=Decimal("764.20"), buying_power=Decimal(0))
    assert gate.check(sell, "SPY", held, LIMITS, NOW).allowed


def test_position_cap_does_not_block_a_sell() -> None:
    # A position over 10% of equity may always be reduced.
    sell = a_card(side="sell", limit_price=Decimal("760.38"))
    held = a_snapshot(position_qty=30, position_value=Decimal("22926.00"))
    assert gate.check(sell, "SPY", held, LIMITS, NOW).allowed


def test_qty_zero_deny_on_a_non_finite_limit() -> None:
    for limit in ("NaN", "Infinity"):
        card = a_card(qty=1, limit_price=Decimal(limit))
        result = gate.check(card, "SPY", a_snapshot(), LIMITS, NOW)
        assert reasons(result) == ("QTY_ZERO",), limit
