"""Settlement and scoring: every buy or sell card is scored 5 sessions after its as_of close.

Definitions, used the same way everywhere (settlement, the breaker, backtests, live-check):
- P0 is the adjusted close of the card's `as_of` session; P5 the adjusted close of the fifth
  session after it, counted on the exchange calendar (weekends and holidays are not sessions).
  A card settles only once that fifth session has closed.
- Raw return R: buy (P5 - P0) / P0; sell (P0 - P5) / P0, so a sell wins when the price falls.
- SPY return over the same two closes; alpha_bps = (R - R_SPY) * 10000.
- won = R > 0. A zero return is not a win, and `ledger.consecutive_losses` counts it as a loss.
- Brier = (confidence - won) ** 2, confidence being the desk's probability its call was right.
- kind: `fill` when the card's order filled any shares, else `counterfactual`. Only fills feed
  the circuit breaker; both are scored.

`score_summary` is a pure function: means plus a paired percentile bootstrap (1,000 resamples
from `random.Random(seed)`) for mean alpha and mean Brier, the 2.5% and 97.5% points taken at
sorted indices floor(0.025 * B) and ceil(0.975 * B) - 1.
"""

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from traderbot.data import Session
from traderbot.proposal import Proposal

HORIZON = 5
BENCHMARK = "SPY"
BPS = Decimal(10000)
RESAMPLES = 1000
SEED = 42
SCORED_SIDES = ("buy", "sell")
SCORED_DESKS = ("single", "full")  # model desks; code built cards carry any other label


@dataclass(frozen=True)
class Settlement:
    digest: str
    symbol: str
    side: str
    desk: str
    kind: str  # fill or counterfactual
    as_of: date
    horizon_end: date
    settled_at: datetime  # the close of the horizon session: when the outcome became known
    p0: Decimal
    p5: Decimal
    spy0: Decimal
    spy5: Decimal
    raw_return: Decimal
    spy_return: Decimal
    alpha_bps: Decimal
    confidence: Decimal
    won: bool
    brier: Decimal


@dataclass(frozen=True)
class ScoreReport:
    settled: int
    win_rate: Decimal | None
    mean_return: Decimal | None
    mean_alpha_bps: Decimal | None
    mean_brier: Decimal | None
    alpha_ci: tuple[Decimal, Decimal] | None
    brier_ci: tuple[Decimal, Decimal] | None
    seed: int
    resamples: int


def price(raw: Decimal, what: str) -> Decimal:
    """A usable price: finite and above zero, or ValueError."""
    if not raw.is_finite() or raw <= 0:
        raise ValueError(f"{what} is {raw}, want a finite price above zero")
    return raw


def horizon_session(sessions: Sequence[Session], as_of: date, now: datetime) -> Session | None:
    """The fifth session after `as_of` once it has closed at `now`, else None."""
    ordered = sorted(sessions, key=lambda s: s.day)
    days = [s.day for s in ordered]
    if as_of not in days:
        raise ValueError(f"{as_of} is not a session in the calendar window")
    index = days.index(as_of) + HORIZON
    if index >= len(ordered) or ordered[index].close > now:
        return None
    return ordered[index]


def forward_return(side: str, p0: Decimal, p5: Decimal) -> Decimal:
    p0, p5 = price(p0, "P0"), price(p5, "P5")
    if side == "buy":
        return (p5 - p0) / p0
    if side == "sell":
        return (p0 - p5) / p0
    raise ValueError(f"only buys and sells are scored, got {side!r}")


def brier(confidence: Decimal, won: bool) -> Decimal:
    if not confidence.is_finite() or not 0 <= confidence <= 1:
        raise ValueError(f"confidence is {confidence}, want 0 to 1")
    return (confidence - (Decimal(1) if won else Decimal(0))) ** 2


def _close(closes: Mapping[date, Decimal], day: date, what: str) -> Decimal:
    if day not in closes:
        raise ValueError(f"no {what} close on {day}")
    return price(closes[day], f"{what} close on {day}")


def settle(
    card: Proposal,
    digest: str,
    kind: str,
    sessions: Sequence[Session],
    closes: Mapping[date, Decimal],
    spy: Mapping[date, Decimal],
    now: datetime,
) -> Settlement | None:
    """The card's settlement once its horizon has closed, None before. ValueError on bad data."""
    if card.side not in SCORED_SIDES:
        raise ValueError(f"only buys and sells are scored, got {card.side!r}")
    if kind not in ("fill", "counterfactual"):
        raise ValueError(f"settlement kind is fill or counterfactual, got {kind!r}")
    if card.confidence is None or not math.isfinite(card.confidence):
        raise ValueError("a scored card needs a finite confidence")
    end = horizon_session(sessions, card.as_of, now)
    if end is None:
        return None
    p0, p5 = _close(closes, card.as_of, card.symbol), _close(closes, end.day, card.symbol)
    spy0, spy5 = _close(spy, card.as_of, BENCHMARK), _close(spy, end.day, BENCHMARK)
    raw = forward_return(card.side, p0, p5)
    spy_return = (spy5 - spy0) / spy0
    confidence = Decimal(str(card.confidence))
    won = raw > 0
    return Settlement(
        digest=digest,
        symbol=card.symbol,
        side=card.side,
        desk=card.desk,
        kind=kind,
        as_of=card.as_of,
        horizon_end=end.day,
        settled_at=end.close,
        p0=p0,
        p5=p5,
        spy0=spy0,
        spy5=spy5,
        raw_return=raw,
        spy_return=spy_return,
        alpha_bps=(raw - spy_return) * BPS,
        confidence=confidence,
        won=won,
        brier=brier(confidence, won),
    )


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / len(values)


def bootstrap_indices(n: int, seed: int, resamples: int) -> list[list[int]]:
    rng = random.Random(seed)
    return [[rng.randrange(n) for _ in range(n)] for _ in range(resamples)]


def percentile_ci(means: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    ordered = sorted(means)
    count = len(ordered)
    return ordered[math.floor(0.025 * count)], ordered[math.ceil(0.975 * count) - 1]


def score_summary(
    settlements: Sequence[Settlement], seed: int = SEED, resamples: int = RESAMPLES
) -> ScoreReport:
    """Win rate, mean return, mean alpha, mean Brier, and bootstrap CIs; None fields when empty."""
    if resamples < 1:
        raise ValueError(f"resamples must be at least 1, got {resamples}")
    n = len(settlements)
    if n == 0:
        return ScoreReport(0, None, None, None, None, None, None, seed, resamples)
    alphas = [s.alpha_bps for s in settlements]
    briers = [s.brier for s in settlements]
    draws = bootstrap_indices(n, seed, resamples)  # paired: one draw resamples both series
    alpha_means = [_mean([alphas[i] for i in draw]) for draw in draws]
    brier_means = [_mean([briers[i] for i in draw]) for draw in draws]
    return ScoreReport(
        settled=n,
        win_rate=Decimal(sum(s.won for s in settlements)) / n,
        mean_return=_mean([s.raw_return for s in settlements]),
        mean_alpha_bps=_mean(alphas),
        mean_brier=_mean(briers),
        alpha_ci=percentile_ci(alpha_means),
        brier_ci=percentile_ci(brier_means),
        seed=seed,
        resamples=resamples,
    )


def _opt(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _ci(pair: tuple[Decimal, Decimal] | None) -> list[str] | None:
    return None if pair is None else [str(pair[0]), str(pair[1])]


def report_json(report: ScoreReport) -> dict[str, Any]:
    """A report as plain JSON values; Decimals as strings, so nothing rounds on the way."""
    return {
        "settled": report.settled,
        "win_rate": _opt(report.win_rate),
        "mean_return": _opt(report.mean_return),
        "mean_alpha_bps": _opt(report.mean_alpha_bps),
        "mean_brier": _opt(report.mean_brier),
        "alpha_ci_bps": _ci(report.alpha_ci),
        "brier_ci": _ci(report.brier_ci),
        "seed": report.seed,
        "resamples": report.resamples,
    }


def _dec(raw: Any) -> Decimal | None:
    return None if raw is None else Decimal(str(raw))


def _pair(raw: Any) -> tuple[Decimal, Decimal] | None:
    return None if raw is None else (Decimal(str(raw[0])), Decimal(str(raw[1])))


def report_from_json(raw: dict[str, Any]) -> ScoreReport:
    return ScoreReport(
        settled=int(raw["settled"]),
        win_rate=_dec(raw["win_rate"]),
        mean_return=_dec(raw["mean_return"]),
        mean_alpha_bps=_dec(raw["mean_alpha_bps"]),
        mean_brier=_dec(raw["mean_brier"]),
        alpha_ci=_pair(raw["alpha_ci_bps"]),
        brier_ci=_pair(raw["brier_ci"]),
        seed=int(raw["seed"]),
        resamples=int(raw["resamples"]),
    )


def independent(settlements: Sequence[Settlement]) -> list[Settlement]:
    """One settlement per (symbol, as_of): cards on the same symbol and session share one
    outcome, so copies would shrink the bootstrap CI without adding evidence. The first known
    one stands. Horizons that overlap across days still correlate; this removes exact copies."""
    seen: set[tuple[str, date]] = set()
    kept: list[Settlement] = []
    for s in sorted(settlements, key=lambda s: (s.settled_at, s.digest)):
        if (s.symbol, s.as_of) not in seen:
            seen.add((s.symbol, s.as_of))
            kept.append(s)
    return kept


def scorable(settlements: Sequence[Settlement]) -> list[Settlement]:
    """Settlements of model desk cards only; code built cards never score the desk."""
    return [s for s in settlements if s.desk in SCORED_DESKS]
