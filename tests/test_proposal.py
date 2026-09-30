"""Sizing in code: buy floors to the tick above the close, sell ceils to the tick below it and
never sells more than the position. Values are worked by hand from the spec's formulas."""

from datetime import datetime
from decimal import Decimal

from support import ET, NEXT_CLOSE, NOW, SESSIONS, a_card

from traderbot import data
from traderbot import proposal as prop

MAX = Decimal(1000)
BAND = Decimal("0.005")


def test_ceil_to_tick_rounds_up_and_keeps_exact_ticks() -> None:
    assert prop.ceil_to_tick(Decimal("760.371")) == Decimal("760.38")
    assert prop.ceil_to_tick(Decimal("760.38")) == Decimal("760.38")
    assert prop.ceil_to_tick(Decimal("0.49751")) == Decimal("0.4976")


def test_buy_sizing_is_unchanged() -> None:
    # 764.20 * 1.005 = 768.021 -> 768.02; floor(1000 / 768.02) = 1.
    assert prop.size(Decimal("764.20"), MAX, BAND) == (Decimal("768.02"), 1)


def test_sell_limit_is_below_the_close_and_capped_by_the_position() -> None:
    # 764.20 * 0.995 = 760.379 -> 760.38; floor(1000 / 760.38) = 1; min(5, 1) = 1.
    assert prop.size(Decimal("764.20"), MAX, BAND, "sell", 5) == (Decimal("760.38"), 1)


def test_sell_without_a_position_sizes_to_zero() -> None:
    assert prop.size(Decimal("764.20"), MAX, BAND, "sell", 0) == (Decimal("760.38"), 0)


def test_sell_qty_is_the_position_when_it_is_smaller() -> None:
    # 50.00 * 0.995 = 49.75; floor(1000 / 49.75) = 20; min(3, 20) = 3.
    assert prop.size(Decimal("50.00"), MAX, BAND, "sell", 3) == (Decimal("49.75"), 3)


def test_next_close_is_the_first_close_after_now() -> None:
    assert data.next_close(SESSIONS, NOW) == NEXT_CLOSE
    during = datetime(2026, 9, 29, 10, 0, tzinfo=ET)
    assert data.next_close(SESSIONS, during) == SESSIONS[0].close


def test_next_close_at_exactly_a_close_is_the_following_session() -> None:
    assert data.next_close(SESSIONS, SESSIONS[0].close) == NEXT_CLOSE


def test_card_round_trips_with_its_expiry() -> None:
    card = a_card()
    text = prop.canonical(card)
    assert prop.canonical(prop.from_json(text)) == text
    assert prop.from_json(text).expires_at == NEXT_CLOSE
