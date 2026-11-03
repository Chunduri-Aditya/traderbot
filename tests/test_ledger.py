"""Ledger guarantees: spend reservations, first decision wins, append only events with redaction,
the equity peak, the loss streak, the breaker latch and the kill switch file."""

import sqlite3
import threading
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from support import ET, NOW, a_card

from traderbot import gate, ledger

CAP = Decimal("2.00")
CALL = Decimal("0.25")


@pytest.fixture
def conn() -> sqlite3.Connection:
    return ledger.connect()


def stored(conn: sqlite3.Connection) -> str:
    return ledger.insert_card(conn, a_card(), Decimal("0.01"), (), gate.Allow())


# Spend reservations


def test_reservation_counts_as_spend_until_it_settles(conn: sqlite3.Connection) -> None:
    rid = ledger.reserve(conn, CALL, CAP, NOW)
    assert ledger.day_spend(conn, NOW.date(), NOW) == CALL
    ledger.settle(conn, rid, Decimal("0.0132"), NOW)
    assert ledger.day_spend(conn, NOW.date(), NOW) == Decimal("0.0132")


def test_reservation_past_the_cap_is_refused(conn: sqlite3.Connection) -> None:
    ledger.settle(conn, ledger.reserve(conn, Decimal("1.80"), CAP, NOW), Decimal("1.80"), NOW)
    with pytest.raises(ledger.SpendCapReached):
        ledger.reserve(conn, CALL, CAP, NOW)  # 1.80 + 0.25 = 2.05 > 2.00
    assert ledger.day_spend(conn, NOW.date(), NOW) == Decimal("1.80")


def test_reservation_reaching_exactly_the_cap_is_allowed(conn: sqlite3.Connection) -> None:
    ledger.settle(conn, ledger.reserve(conn, Decimal("1.75"), CAP, NOW), Decimal("1.75"), NOW)
    ledger.reserve(conn, CALL, CAP, NOW)  # 1.75 + 0.25 = 2.00
    assert ledger.day_spend(conn, NOW.date(), NOW) == CAP


def test_held_reservations_count_against_the_cap(conn: sqlite3.Connection) -> None:
    for _ in range(8):
        ledger.reserve(conn, CALL, CAP, NOW)  # 8 * 0.25 = 2.00 held, none settled
    with pytest.raises(ledger.SpendCapReached):
        ledger.reserve(conn, Decimal("0.01"), CAP, NOW)


def test_missing_or_invalid_cost_is_charged_the_full_reservation(conn: sqlite3.Connection) -> None:
    for bad in (None, Decimal("NaN"), Decimal("-0.01")):
        ledger.settle(conn, ledger.reserve(conn, CALL, CAP, NOW), bad, NOW)
    assert ledger.day_spend(conn, NOW.date(), NOW) == 3 * CALL


def test_dead_reservation_is_charged_in_full_once(conn: sqlite3.Connection) -> None:
    rid = ledger.reserve(conn, CALL, CAP, NOW)
    later = NOW + timedelta(seconds=ledger.RESERVATION_TIMEOUT_S + 1)
    assert ledger.day_spend(conn, NOW.date(), later) == CALL
    ledger.settle(conn, rid, Decimal("0.01"), later)  # too late: already charged in full
    assert ledger.day_spend(conn, NOW.date(), later) == CALL
    notes = conn.execute("SELECT settled_usd, note FROM spend").fetchall()
    assert notes == [("0.25", "expired")]


def test_live_reservation_is_not_charged_early(conn: sqlite3.Connection) -> None:
    ledger.reserve(conn, CALL, CAP, NOW)
    inside = NOW + timedelta(seconds=ledger.RESERVATION_TIMEOUT_S)
    ledger.day_spend(conn, NOW.date(), inside)
    assert conn.execute("SELECT settled_usd FROM spend").fetchall() == [(None,)]


# Decisions


def test_first_decision_wins(conn: sqlite3.Connection) -> None:
    key = stored(conn)
    assert ledger.record_decision(conn, key, "approve", NOW) == "approve"
    assert ledger.record_decision(conn, key, "reject", NOW) == "approve"
    assert conn.execute("SELECT decision FROM decisions").fetchall() == [("approve",)]


def test_a_second_connection_cannot_overwrite_the_decision(conn: sqlite3.Connection) -> None:
    key = stored(conn)
    other = ledger.connect()
    assert ledger.record_decision(other, key, "reject", NOW) == "reject"
    assert ledger.record_decision(conn, key, "approve", NOW) == "reject"


def test_unknown_decision_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        ledger.record_decision(conn, stored(conn), "maybe", NOW)


# Orders


def test_orders_today_counts_submitted_orders_on_that_day(conn: sqlite3.Connection) -> None:
    key = stored(conn)
    ledger.record_order(conn, key, "order-1", "accepted", NOW)
    ledger.record_order(conn, key, "order-1", "filled", NOW + timedelta(days=1), "1", "768.00")
    assert ledger.orders_today(conn, NOW.date()) == 1
    assert ledger.orders_today(conn, NOW.date() + timedelta(days=1)) == 0
    card = ledger.find(conn, key)
    assert (card.order_status, card.filled_qty) == ("filled", "1")


# Events


def test_events_are_append_only(conn: sqlite3.Connection) -> None:
    ledger.log_event(conn, "propose", NOW, details={"symbol": "SPY"})
    with pytest.raises(sqlite3.DatabaseError, match="append only"):
        conn.execute("UPDATE events SET kind = 'forged'")
    with pytest.raises(sqlite3.DatabaseError, match="append only"):
        conn.execute("DELETE FROM events")
    assert conn.execute("SELECT kind FROM events").fetchall() == [("propose",)]


def test_event_details_are_redacted_at_any_depth(conn: sqlite3.Connection) -> None:
    details = {
        "api_key": "PKAAAA",
        "symbol": "SPY",
        "nested": {"Secret-Key": "s3", "rows": [{"token": "t0", "qty": 1}]},
        "keyboard": "kept",
    }
    ledger.log_event(conn, "debug", NOW, details=details)
    (raw,) = conn.execute("SELECT details_json FROM events").fetchone()
    for secret in ("PKAAAA", "s3", "t0"):
        assert secret not in raw
    assert ledger.redact(details) == {
        "api_key": "[redacted]",
        "symbol": "SPY",
        "nested": {"Secret-Key": "[redacted]", "rows": [{"token": "[redacted]", "qty": 1}]},
        "keyboard": "kept",
    }


# Circuit breaker inputs


def test_equity_peak_is_the_highest_reading(conn: sqlite3.Connection) -> None:
    assert ledger.mark_equity(conn, Decimal(100000), NOW) == Decimal(100000)
    assert ledger.mark_equity(conn, Decimal(104000), NOW) == Decimal(104000)
    assert ledger.mark_equity(conn, Decimal(95000), NOW) == Decimal(104000)


def test_consecutive_losses_count_only_the_latest_losing_fills(conn: sqlite3.Connection) -> None:
    rows = [
        ("a", "fill", "-0.01"),
        ("b", "fill", "0.02"),
        ("c", "counterfactual", "-0.05"),
        ("d", "fill", "-0.03"),
        ("e", "fill", "-0.02"),
    ]
    assert ledger.consecutive_losses(conn) == 0
    for i, (tag, kind, ret) in enumerate(rows):
        key = ledger.insert_card(conn, a_card(rationale=tag), Decimal(0), (), gate.Allow())
        with conn:
            conn.execute(
                "INSERT INTO settlements VALUES (?, ?, ?, ?)",
                (key, kind, (NOW + timedelta(minutes=i)).isoformat(), ret),
            )
    assert ledger.consecutive_losses(conn) == 2  # e and d; c is not a fill; b was a gain


def test_breaker_latches_until_reset(conn: sqlite3.Connection) -> None:
    assert not ledger.breaker_latched(conn)
    ledger.latch_breaker(conn, "3 settled losses in a row", NOW)
    assert ledger.breaker_latched(conn)
    assert ledger.reset_breaker(conn, NOW) == 1
    assert not ledger.breaker_latched(conn)
    assert ledger.reset_breaker(conn, NOW) == 0


def test_kill_switch_file(conn: sqlite3.Connection) -> None:
    assert not ledger.kill_switch_on()
    ledger.set_kill_switch(True)
    assert ledger.kill_switch_on()
    assert (ledger.home() / "OFF").exists()
    ledger.set_kill_switch(False)
    assert not ledger.kill_switch_on()


def test_an_order_claim_counts_against_the_daily_cap(conn: sqlite3.Connection) -> None:
    keys = [
        ledger.insert_card(conn, a_card(rationale=f"card {i}"), Decimal(0), (), gate.Allow())
        for i in range(4)
    ]
    ledger.record_order(conn, keys[0], "order-0", "filled", NOW)
    ledger.claim_order(conn, keys[1], 3, NOW)  # an approve in flight holds the second slot
    ledger.claim_order(conn, keys[2], 3, NOW)  # the third slot is still free
    with pytest.raises(ledger.ClaimRefused, match="DAILY_ORDER_CAP"):
        ledger.claim_order(conn, keys[3], 3, NOW)  # both gates may have read 2 before either claim
    assert ledger.orders_today(conn, NOW.date()) == 3


# A claim taken over on a later day moves to that day and counts against its cap

MONDAY = datetime(2026, 11, 2, 17, 0, tzinfo=ET)  # after Monday's close
TUESDAY = datetime(2026, 11, 3, 10, 0, tzinfo=ET)  # in Tuesday's session
TAKEOVER_PATHS = ("unconfirmed", "stale_submitting")


def monday_claim(conn: sqlite3.Connection, path: str, name: str = "monday claim") -> str:
    """A Monday claim whose submit raised (unconfirmed) or whose approve died (submitting)."""
    key = ledger.insert_card(conn, a_card(rationale=name), Decimal(0), (), gate.Allow())
    ledger.claim_order(conn, key, 3, MONDAY)
    if path == "unconfirmed":
        ledger.unconfirm_claim(conn, key, MONDAY)
    return key


def tuesday_orders(conn: sqlite3.Connection, count: int) -> None:
    for i in range(count):
        card = a_card(rationale=f"tuesday order {i}")
        key = ledger.insert_card(conn, card, Decimal(0), (), gate.Allow())
        ledger.record_order(conn, key, f"order-{i}", "accepted", TUESDAY)


def order_row(conn: sqlite3.Connection, key: str) -> tuple[str, str, str]:
    row = conn.execute(
        "SELECT submitted_day, status, updated_at FROM orders WHERE digest = ?", (key,)
    ).fetchone()
    return (str(row[0]), str(row[1]), str(row[2]))


@pytest.mark.parametrize("path", TAKEOVER_PATHS)
def test_a_monday_claim_taken_over_on_a_full_tuesday_is_refused(
    conn: sqlite3.Connection, path: str
) -> None:
    key = monday_claim(conn, path)
    tuesday_orders(conn, 3)
    before = order_row(conn, key)
    with pytest.raises(ledger.ClaimRefused, match="DAILY_ORDER_CAP"):
        ledger.claim_order(conn, key, 3, TUESDAY)
    assert order_row(conn, key) == before
    assert before[0] == MONDAY.date().isoformat()
    assert ledger.orders_today(conn, TUESDAY.date()) == 3


@pytest.mark.parametrize("path", TAKEOVER_PATHS)
def test_a_monday_claim_taken_over_on_tuesday_moves_to_tuesday(
    conn: sqlite3.Connection, path: str
) -> None:
    key = monday_claim(conn, path)
    tuesday_orders(conn, 2)
    ledger.claim_order(conn, key, 3, TUESDAY)
    assert order_row(conn, key)[:2] == (TUESDAY.date().isoformat(), "submitting")
    assert ledger.orders_today(conn, TUESDAY.date()) == 3
    assert ledger.orders_today(conn, MONDAY.date()) == 0


def test_a_same_day_takeover_keeps_its_place_at_the_cap(conn: sqlite3.Connection) -> None:
    tuesday_orders(conn, 2)
    card = a_card(rationale="stale tuesday claim")
    key = ledger.insert_card(conn, card, Decimal(0), (), gate.Allow())
    ledger.claim_order(conn, key, 3, TUESDAY - timedelta(seconds=ledger.CLAIM_TTL_S + 1))
    assert ledger.orders_today(conn, TUESDAY.date()) == 3  # the cap, this claim included
    ledger.claim_order(conn, key, 3, TUESDAY)
    assert order_row(conn, key)[:2] == (TUESDAY.date().isoformat(), "submitting")
    assert ledger.orders_today(conn, TUESDAY.date()) == 3


def test_two_monday_claims_taken_over_at_once_take_one_tuesday_slot(
    conn: sqlite3.Connection,
) -> None:
    keys = [monday_claim(conn, "unconfirmed", f"monday claim {n}") for n in range(2)]
    tuesday_orders(conn, 2)
    barrier = threading.Barrier(2)
    outcomes: dict[str, str] = {}

    def take_over(key: str) -> None:
        own = ledger.connect()  # its own connection, as a second approve process would have
        try:
            barrier.wait(timeout=10)
            ledger.claim_order(own, key, 3, TUESDAY)
            outcomes[key] = "claimed"
        except ledger.ClaimRefused as err:
            outcomes[key] = str(err)
        except (sqlite3.Error, threading.BrokenBarrierError) as err:  # fails the assert below
            outcomes[key] = repr(err)
        finally:
            own.close()

    threads = [threading.Thread(target=take_over, args=(key,)) for key in keys]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes.values()) == ["claimed", "gate denies it now: DAILY_ORDER_CAP"]
    assert ledger.orders_today(conn, TUESDAY.date()) == 3
