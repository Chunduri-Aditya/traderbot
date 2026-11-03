"""Phase 4: settlement after 5 sessions, alpha against SPY, Brier, the bootstrap, and the loss
streak that trips the breaker. Expected values are worked by hand in the comments."""

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from support import ET, a_card, a_snapshot, trip

from traderbot import broker, cli, data, gate, keychain, ledger, scoring

# Tue 9/29 is as_of; Mon 10/5 is a holiday here, so the fifth session after 9/29 is Wed 10/7.
DAYS = [date(2026, 9, d) for d in (29, 30)] + [date(2026, 10, d) for d in (1, 2, 6, 7, 8)]
CAL = [
    data.Session(
        day=d,
        open=datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET),
        close=datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET),
    )
    for d in DAYS
]
AS_OF = DAYS[0]
END = date(2026, 10, 7)
END_CLOSE = datetime(2026, 10, 7, 16, 0, tzinfo=ET)
QQQ = {AS_OF: Decimal(100), END: Decimal(103), date(2026, 10, 6): Decimal(99)}
SPY = {AS_OF: Decimal(400), END: Decimal(404), date(2026, 10, 6): Decimal(402)}


def card(side: str = "buy", confidence: float = 0.7, **changes: Any) -> Any:
    fields: dict[str, Any] = {"symbol": "QQQ", "requested_symbol": "QQQ"} | changes
    return a_card(side=side, confidence=confidence, **fields)


def settle(
    side: str = "buy", confidence: float = 0.7, now: datetime = END_CLOSE
) -> scoring.Settlement | None:
    return scoring.settle(card(side, confidence), "d" * 64, "fill", CAL, QQQ, SPY, now)


def test_a_buy_settles_with_hand_computed_values() -> None:
    s = settle()
    assert s is not None
    assert s.horizon_end == END and s.settled_at == END_CLOSE
    assert s.raw_return == Decimal("0.03")  # (103 - 100) / 100
    assert s.spy_return == Decimal("0.01")  # (404 - 400) / 400
    assert s.alpha_bps == Decimal(200)  # (0.03 - 0.01) * 10000
    assert s.won and s.brier == Decimal("0.09")  # (0.7 - 1) ** 2


def test_a_sell_wins_only_when_the_price_falls() -> None:
    s = settle("sell", 0.6)
    assert s is not None
    assert s.raw_return == Decimal("-0.03")  # (100 - 103) / 100
    assert s.alpha_bps == Decimal(-400)  # (-0.03 - 0.01) * 10000
    assert not s.won and s.brier == Decimal("0.36")  # (0.6 - 0) ** 2


def test_the_horizon_is_five_sessions_not_five_days_and_skips_the_holiday() -> None:
    assert scoring.horizon_session(CAL, AS_OF, END_CLOSE) == CAL[5]
    assert CAL[5].day == END  # 9/30, 10/1, 10/2, 10/6, 10/7


def test_four_sessions_do_not_settle() -> None:
    assert settle(now=datetime(2026, 10, 6, 23, 59, tzinfo=ET)) is None  # only 4 sessions closed
    assert settle(now=END_CLOSE - timedelta(seconds=1)) is None  # the fifth has not closed
    assert settle(now=END_CLOSE) is not None


def test_a_calendar_too_short_does_not_settle() -> None:
    assert (
        scoring.settle(card(), "d" * 64, "fill", CAL[:5], QQQ, SPY, END_CLOSE + timedelta(days=9))
        is None
    )


@pytest.mark.parametrize(
    ("closes", "spy", "match"),
    [
        ({AS_OF: Decimal(100)}, SPY, "no QQQ close"),
        (QQQ, {END: Decimal(404)}, "no SPY close"),
        (QQQ | {END: Decimal("NaN")}, SPY, "finite price"),
        (QQQ | {AS_OF: Decimal(0)}, SPY, "finite price"),
        (QQQ | {AS_OF: Decimal(-5)}, SPY, "finite price"),
        (QQQ, SPY | {AS_OF: Decimal("Infinity")}, "finite price"),
    ],
)
def test_missing_or_bad_prices_refuse_to_settle(
    closes: dict[date, Decimal], spy: dict[date, Decimal], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        scoring.settle(card(), "d" * 64, "fill", CAL, closes, spy, END_CLOSE)


@pytest.mark.parametrize("side", ["hold", "review"])
def test_only_buys_and_sells_are_scored(side: str) -> None:
    with pytest.raises(ValueError, match="only buys and sells"):
        settle(side)


@pytest.mark.parametrize("confidence", [float("nan"), 1.5, -0.1])
def test_a_confidence_outside_zero_to_one_refuses(confidence: float) -> None:
    with pytest.raises(ValueError):
        settle(confidence=confidence)


def test_brier_boundaries() -> None:
    assert scoring.brier(Decimal(1), True) == 0
    assert scoring.brier(Decimal(1), False) == 1
    assert scoring.brier(Decimal("0.5"), False) == Decimal("0.25")


def a_settlement(
    alpha: str, brier: str = "0.25", won: bool = True, desk: str = "full"
) -> scoring.Settlement:
    s = settle()
    assert s is not None
    return replace(s, alpha_bps=Decimal(alpha), brier=Decimal(brier), won=won, desk=desk)


def test_summary_of_nothing_is_empty_not_zero() -> None:
    report = scoring.score_summary([])
    assert report.settled == 0 and report.mean_alpha_bps is None and report.alpha_ci is None


def test_summary_means_and_win_rate_by_hand() -> None:
    rows = [
        a_settlement("100", "0.04", True),
        a_settlement("-50", "0.36", False),
        a_settlement("10", "0.09", True),
        a_settlement("0", "0.25", False),
    ]
    report = scoring.score_summary(rows)
    assert report.settled == 4
    assert report.win_rate == Decimal("0.5")
    assert report.mean_alpha_bps == Decimal(15)  # (100 - 50 + 10 + 0) / 4
    assert report.mean_brier == Decimal("0.185")  # (0.04 + 0.36 + 0.09 + 0.25) / 4
    assert report.alpha_ci is not None and report.alpha_ci[0] <= Decimal(15) <= report.alpha_ci[1]
    assert Decimal(-50) <= report.alpha_ci[0] and report.alpha_ci[1] <= Decimal(100)


def test_bootstrap_is_deterministic_by_seed() -> None:
    rows = [a_settlement(str(a)) for a in (-120, -30, 5, 40, 90, 250)]
    assert scoring.score_summary(rows, seed=7) == scoring.score_summary(rows, seed=7)
    assert (
        scoring.score_summary(rows, seed=7).alpha_ci != scoring.score_summary(rows, seed=8).alpha_ci
    )


def test_one_settlement_has_a_point_ci() -> None:
    report = scoring.score_summary([a_settlement("33")])
    assert report.alpha_ci == (Decimal(33), Decimal(33))


def test_percentile_indices_are_25_and_974_of_1000() -> None:
    assert scoring.percentile_ci([Decimal(i) for i in range(1000)]) == (Decimal(25), Decimal(974))


def test_code_built_cards_never_score_the_desk() -> None:
    rows = [
        a_settlement("10", desk="full"),
        a_settlement("10", desk="single"),
        a_settlement("10", desk="test"),
        a_settlement("10", desk="control"),
    ]
    assert [s.desk for s in scoring.scorable(rows)] == ["full", "single"]


# The ledger and the breaker


@pytest.fixture
def conn() -> sqlite3.Connection:
    return ledger.connect()


def store(
    conn: sqlite3.Connection,
    ret: str,
    kind: str = "fill",
    minutes: int = 0,
    at: datetime = END_CLOSE,
) -> str:
    c = card(
        rationale=f"r{ret}{minutes}{kind}{at}",
        created_at=datetime(2026, 9, 29, 18, 0, tzinfo=ET) + timedelta(minutes=minutes),
    )
    key = ledger.insert_card(conn, c, Decimal(0), (), gate.Allow())
    s = scoring.settle(c, key, kind, CAL, QQQ, SPY, END_CLOSE)
    assert s is not None
    ledger.record_settlement(
        conn, replace(s, raw_return=Decimal(ret), won=Decimal(ret) > 0, settled_at=at)
    )
    return key


def test_settlement_round_trips_and_is_written_once(conn: sqlite3.Connection) -> None:
    c = card()
    key = ledger.insert_card(conn, c, Decimal(0), (), gate.Allow())
    s = scoring.settle(c, key, "fill", CAL, QQQ, SPY, END_CLOSE)
    assert s is not None
    assert ledger.record_settlement(conn, s)
    assert not ledger.record_settlement(conn, s)
    assert ledger.settlements(conn) == [s]
    assert ledger.unsettled(conn) == []


def test_three_settled_losing_fills_trip_the_breaker(conn: sqlite3.Connection) -> None:
    for i in range(3):
        store(conn, "-0.01", minutes=i, at=END_CLOSE + timedelta(days=i))
    losses = ledger.consecutive_losses(conn)
    assert losses == 3
    verdict = gate.check(
        a_card(),
        "SPY",
        a_snapshot(consecutive_losses=losses),
        gate.Limits(),
        datetime(2026, 9, 29, 18, 0, tzinfo=ET),
    )
    assert isinstance(verdict, gate.Deny) and "CIRCUIT_BREAKER" in verdict.reasons


def test_two_losses_after_a_win_do_not_trip(conn: sqlite3.Connection) -> None:
    store(conn, "-0.01", minutes=0, at=END_CLOSE)
    store(conn, "0.02", minutes=1, at=END_CLOSE + timedelta(days=1))
    store(conn, "-0.01", minutes=2, at=END_CLOSE + timedelta(days=2))
    store(conn, "-0.01", minutes=3, at=END_CLOSE + timedelta(days=3))
    assert ledger.consecutive_losses(conn) == 2


def test_a_zero_return_is_a_loss_like_it_is_not_a_win(conn: sqlite3.Connection) -> None:
    for i in range(3):
        store(conn, "0", minutes=i, at=END_CLOSE + timedelta(days=i))
    assert ledger.consecutive_losses(conn) == 3


def test_counterfactual_losses_never_trip_the_breaker(conn: sqlite3.Connection) -> None:
    for i in range(3):
        store(conn, "-0.05", kind="counterfactual", minutes=i, at=END_CLOSE + timedelta(days=i))
    assert ledger.consecutive_losses(conn) == 0


def test_a_batch_settled_at_one_close_counts_in_card_order(conn: sqlite3.Connection) -> None:
    store(conn, "-0.01", minutes=0)
    store(conn, "-0.01", minutes=1)
    store(conn, "0.03", minutes=2)  # the newest card, settled at the same close, won
    assert ledger.consecutive_losses(conn) == 0


def test_fill_kind_follows_the_filled_quantity(conn: sqlite3.Connection) -> None:
    key = ledger.insert_card(conn, card(), Decimal(0), (), gate.Allow())
    assert ledger.settlement_kind(ledger.find(conn, key)) == "counterfactual"
    ledger.record_order(conn, key, "o1", "partially_filled", END_CLOSE, "1", "100")
    assert ledger.settlement_kind(ledger.find(conn, key)) == "fill"


# The CLI


@pytest.fixture
def alpaca(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    fetched: list[str] = []

    def history(
        keys: Any, symbol: str, start: date, as_of: Any, now: Any, adjustment: Any
    ) -> list[tuple[date, Decimal]]:
        fetched.append(symbol)
        return sorted({"QQQ": QQQ, "SPY": SPY}.get(symbol, {}).items())

    monkeypatch.setattr(keychain, "load", lambda: keychain.AlpacaKeys("key", "secret"))
    monkeypatch.setattr(broker, "client", lambda keys: object())
    monkeypatch.setattr(data, "cached_sessions", lambda trading, start, end, now: CAL)
    monkeypatch.setattr(data, "history", history)
    monkeypatch.setattr(data, "now_et", lambda: END_CLOSE + timedelta(hours=1))
    return fetched


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, Any]:
    assert cli.main([*argv, "--json"]) == 0
    out: dict[str, Any] = json.loads(capsys.readouterr().out)
    return out


def test_settle_command_settles_waits_and_skips(
    conn: sqlite3.Connection, alpaca: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    ready = ledger.insert_card(conn, card(), Decimal(0), (), gate.Allow())
    young = ledger.insert_card(
        conn, card(as_of=date(2026, 10, 6), rationale="young"), Decimal(0), (), gate.Allow()
    )
    broken = ledger.insert_card(
        conn,
        card(symbol="IWM", requested_symbol="IWM", rationale="no bar"),
        Decimal(0),
        (),
        gate.Allow(),
    )
    ledger.insert_card(conn, card(side="hold", rationale="held"), Decimal(0), (), gate.Allow())
    view = run(capsys, "settle")
    assert [row["digest"] for row in view["settled"]] == [ready]
    assert Decimal(view["settled"][0]["alpha_bps"]) == 200
    assert view["waiting"] == [young[:8]]
    assert [row["digest"] for row in view["skipped"]] == [broken[:8]]
    assert "no IWM close" in view["skipped"][0]["reason"]
    assert sorted(alpaca) == ["IWM", "QQQ", "SPY"]  # each series read once


def test_score_command_reads_only_the_ledger(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(keychain, "load", trip)
    c = card(desk="full")
    key = ledger.insert_card(conn, c, Decimal(0), (), gate.Allow())
    s = scoring.settle(c, key, "counterfactual", CAL, QQQ, SPY, END_CLOSE)
    assert s is not None
    ledger.record_settlement(conn, s)
    view = run(capsys, "score")
    assert view["settled"] == 1 and Decimal(view["mean_alpha_bps"]) == 200
    assert [Decimal(x) for x in view["alpha_ci_bps"]] == [200, 200]


def test_settle_with_nothing_pending_reads_no_keys(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(keychain, "load", trip)
    assert run(capsys, "settle") == {"settled": [], "waiting": [], "skipped": []}
