"""Phase 6: the rules baselines equal hand computed values, see no future bar, go through the
gate (the breaker included), and the CLI stores a summary live-check can read."""

import json
import math
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from support import ET, trip

from traderbot import backtest, broker, cli, data, keychain, ledger


def weekdays(n: int, start: date = date(2026, 1, 5)) -> list[date]:
    out: list[date] = []
    day = start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def series(prices: Sequence[int | str]) -> list[tuple[date, Decimal]]:
    return list(zip(weekdays(len(prices)), (Decimal(str(p)) for p in prices), strict=True))


def flat_spy(
    closes: list[tuple[date, Decimal]], bump_from: int | None = None
) -> dict[date, Decimal]:
    return {
        d: Decimal(404 if bump_from is not None and i >= bump_from else 400)
        for i, (d, _) in enumerate(closes)
    }


SMA = series([100] * 30 + [110] * 5 + [121] * 2)


def test_sma_cross_by_hand() -> None:
    # Bar 30: the 10 bar SMA goes 100 -> 101 and the 30 bar 100 -> 100.33, a cross up: buy.
    # Limit floor(110 * 1.005) = 110.55, qty floor(1000 / 110.55) = 9. P0 110, P5 (bar 35) 121:
    # R = 0.1; SPY 400 -> 404 is 0.01; alpha 900 bps. Equity 100000 - 9 * 110.55 + 9 * 121.
    report = backtest.run("QQQ", SMA, flat_spy(SMA, bump_from=35), backtest.STRATEGIES["sma"])
    assert [(t.day, t.side, t.qty, t.limit_price, t.allowed) for t in report.trades] == [
        (SMA[30][0], "buy", 9, Decimal("110.55"), True)
    ]
    s = report.trades[0].settlement
    assert s is not None and s.desk == "baseline_sma"
    assert s.raw_return == Decimal("0.1") and s.spy_return == Decimal("0.01")
    assert s.alpha_bps == Decimal(900)
    assert report.final_equity == Decimal("100094.05")
    assert report.buy_and_hold_return == Decimal("0.21") and report.spy_return == Decimal("0.01")
    assert report.summary.settled == 1 and report.open_orders == 0


def test_mean_reversion_by_hand() -> None:
    # Bar 19: mean 99, population sd sqrt(19), z = -4.36: buy 12 at floor(80 * 1.005) = 80.40.
    # Bars 20 to 22 stay under their 20 bar mean (98.25, 97.75, 97.5). Bar 23 closes 100 over
    # 97.5: sell min(12, floor(1000 / 99.50)) = 10 at ceil(100 * 0.995) = 99.50; bar 24 sells the
    # last 2. Buy R = (100 - 80) / 80 = 0.25 (2500 bps against a flat SPY); the bar 23 sell
    # R = 0, not a win; the bar 24 sell has no fifth bar and stays open.
    closes = series([100] * 19 + [80, 85, 90, 95, 100, 100, 100, 100, 100, 100])
    report = backtest.run("QQQ", closes, flat_spy(closes), backtest.STRATEGIES["mean_rev"])
    got = [
        (i, t.side, t.qty, t.limit_price)
        for t in report.trades
        for i, (d, _) in enumerate(closes)
        if d == t.day
    ]
    assert got == [
        (19, "buy", 12, Decimal("80.40")),
        (23, "sell", 10, Decimal("99.50")),
        (24, "sell", 2, Decimal("99.50")),
    ]
    assert [t.settlement.raw_return if t.settlement else None for t in report.trades] == [
        Decimal("0.25"),
        Decimal(0),
        None,
    ]
    assert report.summary.settled == 2 and report.open_orders == 1
    assert report.summary.mean_alpha_bps == Decimal(1250)  # (2500 + 0) / 2
    assert report.summary.win_rate == Decimal("0.5")


def wavy(n: int) -> list[tuple[date, Decimal]]:
    """A 50 bar cycle with a slight drift and a one day drop every 37 bars: both baselines trade."""
    return series(
        [
            str(
                round(
                    100
                    + 15 * math.sin(2 * math.pi * i / 50)
                    + (-14 if i % 37 == 36 else 0)
                    + i * 0.05,
                    2,
                )
            )
            for i in range(n)
        ]
    )


@pytest.mark.parametrize("name", ["sma", "mean_rev"])
def test_no_decision_sees_a_later_bar(name: str) -> None:
    closes = wavy(160)
    cut = 110  # after sma trades at bars 50, 75 and 100 and mean_rev's dips
    changed = closes[: cut + 1] + [(d, c * 3) for d, c in closes[cut + 1 :]]
    strategy = backtest.STRATEGIES[name]
    before = backtest.run("QQQ", closes, flat_spy(closes), strategy)
    after = backtest.run("QQQ", changed, flat_spy(changed), strategy)

    def decisions(r: backtest.BacktestReport) -> list[Any]:
        return [
            (t.day, t.side, t.qty, t.limit_price, t.allowed, t.reasons)
            for t in r.trades
            if t.day <= closes[cut][0]
        ]

    assert decisions(before) and decisions(before) == decisions(after)


def always_buy(prices: Sequence[Decimal], held: bool) -> str:
    return "buy"


def test_the_breaker_stops_the_baseline_after_three_settled_losses() -> None:
    # Buys on bars 0, 1 and 2 lose; their fifth bars (5, 6, 7) close before the next decision,
    # so the third loss is known at bar 7, and the gate denies from then on.
    closes = series(list(range(100, 80, -1)))
    strategy = backtest.Strategy("always", "baseline_test", always_buy)
    report = backtest.run("QQQ", closes, flat_spy(closes), strategy)
    allowed = [t.allowed for t in report.trades]
    assert allowed[:7] == [True] * 7 and not any(allowed[7:])
    assert "CIRCUIT_BREAKER" in report.trades[7].reasons
    assert dict(report.deny_reasons)["CIRCUIT_BREAKER"] == 13


def test_the_position_cap_binds_the_baseline() -> None:
    closes = series(list(range(100, 140)))  # every buy wins, so the breaker never trips
    report = backtest.run(
        "QQQ", closes, flat_spy(closes), backtest.Strategy("a", "baseline_test", always_buy)
    )
    reasons = dict(report.deny_reasons)
    assert "POSITION_OVER_MAX_PCT" in reasons and "CIRCUIT_BREAKER" not in reasons
    first_cap = next(t for t in report.trades if not t.allowed)
    assert first_cap.reasons == ("POSITION_OVER_MAX_PCT",)
    assert all(not t.allowed for t in report.trades[report.trades.index(first_cap) :])


@pytest.mark.parametrize(
    "closes",
    [
        series([100]),
        list(reversed(series([1, 2, 3]))),
        series([1, 2]) + [series([1, 2])[1]],
        series([100, "NaN", 101]),
        series([100, 0, 101]),
        series([100, -1, 101]),
    ],
)
def test_bad_history_is_refused(closes: list[tuple[date, Decimal]]) -> None:
    with pytest.raises(ValueError):
        backtest.run("QQQ", closes, flat_spy(closes), backtest.STRATEGIES["sma"])


# The CLI

DAYS = [d for d, _ in SMA]
CAL = [
    data.Session(
        d,
        datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET),
        datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET),
    )
    for d in DAYS
]


@pytest.fixture
def alpaca(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keychain, "load", lambda: keychain.AlpacaKeys("key", "secret"))
    monkeypatch.setattr(broker, "client", lambda keys: object())
    monkeypatch.setattr(data, "cached_sessions", lambda trading, start, end, now: CAL)
    monkeypatch.setattr(
        data,
        "history",
        lambda keys, symbol, start, as_of, now, adj: (
            SMA if symbol == "QQQ" else sorted(flat_spy(SMA, 35).items())
        ),
    )
    monkeypatch.setattr(
        data,
        "now_et",
        lambda: datetime.combine(DAYS[-1], datetime.min.time(), ET) + timedelta(hours=18),
    )


def test_backtest_command_stores_the_baseline_summary(
    alpaca: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["backtest", "QQQ", "--json"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["allowed"] == 1 and Decimal(view["summary"]["mean_alpha_bps"]) == 900
    stored = ledger.latest_baseline(ledger.connect(), "sma")
    assert stored is not None and stored.mean_alpha_bps == Decimal(900)


@pytest.mark.parametrize("argv", [["backtest", "qq$"], ["backtest", "QQQ", "--days", "10"]])
def test_backtest_refuses_bad_input_before_any_key_read(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(keychain, "load", trip)
    assert cli.main(argv) == 1
    assert capsys.readouterr().err.startswith("refused:")


def test_live_check_fails_on_a_young_ledger_and_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(keychain, "load", trip)
    assert cli.main(["live-check", "--json"]) == 1
    view = json.loads(capsys.readouterr().out)
    assert not view["passed"]
    assert view["reasons"] == [
        "INSUFFICIENT_SETTLEMENTS",
        "NEGATIVE_ALPHA_CI",
        "BRIER_OVER_MAX",
        "NO_BASELINE",
    ]
    assert cli.main(["live-check"]) == 1
    assert capsys.readouterr().out.startswith("live-check FAIL: INSUFFICIENT_SETTLEMENTS")


def test_live_check_sees_the_stored_baseline_and_the_switch(
    alpaca: None, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["backtest", "QQQ", "--json"])
    ledger.set_kill_switch(True)
    capsys.readouterr()
    assert cli.main(["live-check", "--json"]) == 1
    reasons = json.loads(capsys.readouterr().out)["reasons"]
    assert "NO_BASELINE" in reasons  # one settled baseline trade is thinner than 20
    assert "KILL_SWITCH_ACTIVE" in reasons
