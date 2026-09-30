"""A card: what the desk chose plus what code computed. Its sha256 is the order's client id.

The model chooses only symbol, side, rationale and confidence (`DeskChoice`). Code computes the
quantity and the limit price from the last raw close, in Decimal, flooring to the tick so that
qty * limit never exceeds the max notional.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SIDES = ("buy", "sell", "hold", "review")  # buy, sell and hold come from the desk; review from code
TICK = Decimal("0.01")  # at $1 and above
SUB_DOLLAR_TICK = Decimal("0.0001")


class DeskChoice(BaseModel):
    """The only fields the model may choose. Anything else fails validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str = Field(max_length=10)
    side: Literal["buy", "sell", "hold"]
    rationale: str = Field(max_length=1000)
    confidence: float = Field(ge=0, le=1)


@dataclass(frozen=True)
class Proposal:
    symbol: str  # the desk's answer; the gate compares it with requested_symbol
    requested_symbol: str  # what `propose` was asked for; "" on cards stored before it existed
    side: str
    rationale: str
    confidence: float | None  # None only on review
    qty: int
    limit_price: Decimal
    order_type: str
    time_in_force: str
    as_of: date
    bar_date: date
    last_close: Decimal
    desk: str
    model: str
    created_at: datetime
    expires_at: datetime | None  # the next session close; None only on cards before Phase 2


def floor_to_tick(price: Decimal) -> Decimal:
    tick = TICK if price >= 1 else SUB_DOLLAR_TICK
    return (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def ceil_to_tick(price: Decimal) -> Decimal:
    tick = TICK if price >= 1 else SUB_DOLLAR_TICK
    return (price / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def size(
    last_close: Decimal,
    max_notional: Decimal,
    band: Decimal,
    side: str = "buy",
    position_qty: int = 0,
) -> tuple[Decimal, int]:
    """(limit, qty) in code, never from the model.

    Buy, and hold or review as a counterfactual buy: limit = floor_to_tick(last_close * (1 + band)),
    qty = floor(max / limit). Sell: limit = ceil_to_tick(last_close * (1 - band)), qty = the smaller
    of the position and floor(max / limit), so a sell never goes short.
    """
    if side == "sell":
        limit = ceil_to_tick(last_close * (1 - band))
        if limit <= 0:
            return limit, 0
        most = int((max_notional / limit).to_integral_value(rounding=ROUND_FLOOR))
        return limit, max(0, min(position_qty, most))
    limit = floor_to_tick(last_close * (1 + band))
    if limit <= 0:
        return limit, 0
    return limit, int((max_notional / limit).to_integral_value(rounding=ROUND_FLOOR))


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def canonical(proposal: Proposal) -> str:
    fields = {key: _plain(value) for key, value in asdict(proposal).items()}
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def digest(proposal: Proposal) -> str:
    return digest_of(canonical(proposal))


def from_json(text: str) -> Proposal:
    raw = json.loads(text)
    return Proposal(
        symbol=raw["symbol"],
        requested_symbol=raw.get("requested_symbol", ""),
        side=raw["side"],
        rationale=raw["rationale"],
        confidence=raw["confidence"],
        qty=raw["qty"],
        limit_price=Decimal(raw["limit_price"]),
        order_type=raw["order_type"],
        time_in_force=raw["time_in_force"],
        as_of=date.fromisoformat(raw["as_of"]),
        bar_date=date.fromisoformat(raw["bar_date"]),
        last_close=Decimal(raw["last_close"]),
        desk=raw["desk"],
        model=raw["model"],
        created_at=datetime.fromisoformat(raw["created_at"]),
        expires_at=_moment(raw.get("expires_at")),
    )


def _moment(raw: str | None) -> datetime | None:
    return None if raw is None else datetime.fromisoformat(raw)
