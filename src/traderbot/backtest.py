"""A deterministic walk forward backtest of rules baselines, through the same sizing, gate and
settlement code as the live path.

On each bar `i` the strategy sees closes 0..i only, decides at that close, and code sizes the
order with `proposal.size`. The card then goes through `gate.check` against a simulated account:
cash as buying power, equity and its running peak, the position, and the loss streak of
simulated fills whose 5 session horizon had already closed by then (a later outcome is never
visible early). An allowed order fills at its limit price, which is the band away from the close
and against the trader, so the fill is conservative. Every allowed order settles exactly as a
live card does (`scoring.settle`: P0 the decision close, P5 five bars later, SPY over the same
bars), so baseline alpha and desk alpha share one definition. No commissions are modelled
(Alpaca charges none on US equities); orders within the last 5 bars stay open, unscored.

Baselines (cards labelled for scoring to leave out):
- `sma` (`baseline_sma`): buy when the 10 bar SMA crosses above the 30 bar SMA; sell what is held
  when it crosses back below; else hold.
- `mean_rev` (`baseline_mean_rev`): buy when not holding and the close is 2 or more population
  standard deviations below its 20 bar mean; sell what is held once the close is back at or
  above that mean; else hold.
- buy and hold is reported alongside as the reference, not traded.

A baseline's confidence is fixed at 0.5, so its Brier is 0.25 by construction and means nothing.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from traderbot import gate, scoring
from traderbot import proposal as prop
from traderbot.data import ET, Session

Signal = Callable[[Sequence[Decimal], bool], str]
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)
DECIDED_AFTER = timedelta(minutes=30)  # past the 16 minute SIP delay, as a live propose would be
CONFIDENCE = 0.5
INITIAL_CASH = Decimal(100000)


@dataclass(frozen=True)
class Strategy:
    name: str
    desk_label: str
    signal: Signal


@dataclass(frozen=True)
class SimTrade:
    day: date
    side: str
    qty: int
    limit_price: Decimal
    allowed: bool
    reasons: tuple[str, ...]
    settlement: scoring.Settlement | None


@dataclass(frozen=True)
class BacktestReport:
    symbol: str
    strategy: str
    desk_label: str
    start: date
    end: date
    bars: int
    signals: int
    allowed: int
    denied: int
    deny_reasons: tuple[tuple[str, int], ...]
    open_orders: int  # allowed within the last 5 bars, not yet settled
    final_equity: Decimal
    buy_and_hold_return: Decimal
    spy_return: Decimal | None
    summary: scoring.ScoreReport  # settled allowed trades
    trades: tuple[SimTrade, ...]


def sma(prices: Sequence[Decimal], n: int) -> Decimal:
    return sum(prices[-n:], Decimal(0)) / n


def sma_cross(prices: Sequence[Decimal], held: bool) -> str:
    if len(prices) < 31:
        return "hold"
    fast, slow = sma(prices, 10), sma(prices, 30)
    was_fast, was_slow = sma(prices[:-1], 10), sma(prices[:-1], 30)
    if was_fast <= was_slow and fast > slow:
        return "buy"
    if held and was_fast >= was_slow and fast < slow:
        return "sell"
    return "hold"


def mean_reversion(prices: Sequence[Decimal], held: bool) -> str:
    if len(prices) < 20:
        return "hold"
    window = prices[-20:]
    mean = sum(window, Decimal(0)) / 20
    std = (sum(((p - mean) ** 2 for p in window), Decimal(0)) / 20).sqrt()
    if held:
        return "sell" if prices[-1] >= mean else "hold"
    if std > 0 and (prices[-1] - mean) / std <= -2:
        return "buy"
    return "hold"


STRATEGIES = {
    "sma": Strategy("sma", "baseline_sma", sma_cross),
    "mean_rev": Strategy("mean_rev", "baseline_mean_rev", mean_reversion),
}


def _at(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, ET)


def _validate(closes: Sequence[tuple[date, Decimal]]) -> None:
    if len(closes) < 2:
        raise ValueError("a backtest needs at least two bars")
    days = [day for day, _ in closes]
    if days != sorted(set(days)):
        raise ValueError("bars must be in date order with one close per day")
    for day, close in closes:
        scoring.price(close, f"close on {day}")


def _streak(settled: Sequence[scoring.Settlement], now: datetime) -> int:
    """Simulated fills known by `now` that did not win, latest first, in a row."""
    known = sorted((s for s in settled if s.settled_at <= now), key=lambda s: s.settled_at)
    streak = 0
    for s in reversed(known):
        if s.raw_return > 0:
            break
        streak += 1
    return streak


def run(
    symbol: str,
    closes: Sequence[tuple[date, Decimal]],
    spy: Mapping[date, Decimal],
    strategy: Strategy,
    limits: gate.Limits = gate.Limits(),  # noqa: B008 (frozen dataclass)
    initial_cash: Decimal = INITIAL_CASH,
) -> BacktestReport:
    """Walk `closes` (adjusted, oldest first) bar by bar; pure: no clock, no I/O."""
    _validate(closes)
    days = [day for day, _ in closes]
    by_day = dict(closes)
    sessions = [
        Session(day=d, open=_at(d, SESSION_OPEN), close=_at(d, SESSION_CLOSE)) for d in days
    ]
    end_of_data = _at(days[-1], SESSION_CLOSE) + DECIDED_AFTER
    cash, held, peak = initial_cash, 0, initial_cash
    trades: list[SimTrade] = []
    settled: list[scoring.Settlement] = []
    for i, (day, close) in enumerate(closes):
        equity = cash + held * close
        peak = max(peak, equity)
        side = strategy.signal([c for _, c in closes[: i + 1]], held > 0)
        if side == "hold":
            continue
        limit, qty = prop.size(close, limits.max_notional, limits.band, side, held)
        now = _at(day, SESSION_CLOSE) + DECIDED_AFTER
        expires = _at(days[i + 1], SESSION_CLOSE) if i + 1 < len(days) else now + timedelta(days=1)
        card = prop.Proposal(
            symbol=symbol,
            requested_symbol=symbol,
            side=side,
            rationale=f"{strategy.name} {side}",
            confidence=CONFIDENCE,
            qty=qty,
            limit_price=limit,
            order_type="limit",
            time_in_force="day",
            as_of=day,
            bar_date=day,
            last_close=close,
            desk=strategy.desk_label,
            model="rules",
            created_at=now,
            expires_at=expires,
        )
        snapshot = gate.AccountSnapshot(
            buying_power=cash,
            equity=equity,
            equity_peak=peak,
            position_qty=held,
            position_value=held * close,
            open_order_symbols=frozenset(),
            expected_as_of=day,
            day_spend_usd=Decimal(0),
            orders_today=0,
            consecutive_losses=_streak(settled, now),
            kill_switch=False,
            breaker_latched=False,
        )
        verdict = gate.check(card, symbol, snapshot, limits, now)
        if isinstance(verdict, gate.Deny):
            trades.append(SimTrade(day, side, qty, limit, False, verdict.reasons, None))
            continue
        if side == "buy":
            cash -= qty * limit
            held += qty
        else:
            cash += qty * limit
            held -= qty
        result = scoring.settle(card, prop.digest(card), "fill", sessions, by_day, spy, end_of_data)
        if result is not None:
            settled.append(result)
        trades.append(SimTrade(day, side, qty, limit, True, (), result))
    allowed = [t for t in trades if t.allowed]
    reasons = Counter(code for t in trades for code in t.reasons)
    first, last = closes[0][1], closes[-1][1]
    spy_return = None
    if days[0] in spy and days[-1] in spy:
        spy0 = scoring.price(spy[days[0]], "SPY first close")
        spy_return = (scoring.price(spy[days[-1]], "SPY last close") - spy0) / spy0
    return BacktestReport(
        symbol=symbol,
        strategy=strategy.name,
        desk_label=strategy.desk_label,
        start=days[0],
        end=days[-1],
        bars=len(days),
        signals=len(trades),
        allowed=len(allowed),
        denied=len(trades) - len(allowed),
        deny_reasons=tuple(sorted(reasons.items())),
        open_orders=sum(1 for t in allowed if t.settlement is None),
        final_equity=cash + held * last,
        buy_and_hold_return=(last - first) / first,
        spy_return=spy_return,
        summary=scoring.score_summary(settled),
        trades=tuple(trades),
    )
