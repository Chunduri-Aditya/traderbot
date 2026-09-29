"""The single desk: one headless claude call on a clean profile, answering through a schema.

No tools, no MCP, no settings but the project's (and the cwd is a fresh temp dir), an env
allowlist, the prompt on stdin, a pinned model that must be the only one in `modelUsage`. Any
failure (timeout, bad JSON, error envelope, wrong stop, wrong model, schema) is REVIEW, never
hold. The profile follows twin's clean profile (/Users/chunduri/Desktop/twin/src/twin/adapters.py
flags at 46-60, env at 25 and 200-205, stop reasons at 67-70), rebuilt here.
"""

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from traderbot.data import Bars
from traderbot.proposal import DeskChoice

CLAUDE_BIN = "claude"
MODEL = "claude-sonnet-5-5"
EFFORT = "low"
CALL_CAP_USD = Decimal("0.25")
TIMEOUT_S = 300
ENV_ALLOW = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR")
CLEAN_FLAGS = (
    "-p",
    "--output-format",
    "json",
    "--tools",
    "",
    "--strict-mcp-config",
    "--setting-sources",
    "project",
    "--no-session-persistence",
    "--disable-slash-commands",
    "--safe-mode",
    "--permission-mode",
    "dontAsk",
)
# With a schema the structured reply arrives through a tool call, so tool_use is a normal stop.
SCHEMA_STOPS = ("end_turn", "tool_use")


@dataclass(frozen=True)
class DeskResult:
    choice: DeskChoice | None  # None means REVIEW
    review_reason: str | None
    cost_usd: Decimal
    model_usage: tuple[str, ...]


def argv() -> list[str]:
    schema = json.dumps(DeskChoice.model_json_schema(), separators=(",", ":"))
    return [
        CLAUDE_BIN,
        *CLEAN_FLAGS,
        "--model",
        MODEL,
        "--effort",
        EFFORT,
        "--max-budget-usd",
        str(CALL_CAP_USD),
        "--json-schema",
        schema,
    ]


def env() -> dict[str, str]:
    allowed = {name: os.environ[name] for name in ENV_ALLOW if name in os.environ}
    return allowed | {"NO_COLOR": "1"}


def prompt(bars: Bars) -> str:
    rows = "\n".join(f"{day.isoformat()} {close}" for day, close in bars.closes)
    return (
        "You are the single analyst desk of a personal paper trading bot. Decide whether to buy "
        f"{bars.symbol} with a DAY limit order just above the last close, to hold for about 5 "
        "sessions, or to hold off.\n\n"
        f"Daily raw closes for {bars.symbol}, oldest first; the last complete session is "
        f"{bars.as_of.isoformat()}:\n{rows}\n\n"
        f"Answer through the schema: symbol (exactly {bars.symbol}), side (buy or hold), "
        "rationale (two or three sentences on the evidence above), confidence (0 to 1, your "
        "probability that the decision is right)."
    )


def _review(reason: str, cost: Decimal, usage: tuple[str, ...] = ()) -> DeskResult:
    return DeskResult(choice=None, review_reason=reason, cost_usd=cost, model_usage=usage)


def _cost(envelope: dict[str, Any]) -> Decimal:
    """A missing or non numeric cost is charged at the call cap."""
    raw = envelope.get("total_cost_usd")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
        return CALL_CAP_USD
    return Decimal(str(raw))


def parse(stdout: str, returncode: int) -> DeskResult:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return _review(f"claude exit {returncode}, stdout is not json", CALL_CAP_USD)
    if not isinstance(envelope, dict):
        return _review(f"stdout json is {type(envelope).__name__}, want object", CALL_CAP_USD)
    cost = _cost(envelope)
    usage_raw = envelope.get("modelUsage")
    usage = tuple(sorted(usage_raw)) if isinstance(usage_raw, dict) else ()
    if returncode != 0 or envelope.get("is_error") is not False:
        return _review(
            f"claude exit {returncode}, is_error {envelope.get('is_error')}", cost, usage
        )
    if envelope.get("stop_reason") not in SCHEMA_STOPS:
        return _review(f"stop_reason {envelope.get('stop_reason')!r} not allowed", cost, usage)
    if usage != (MODEL,):
        return _review(f"model mismatch: pinned {MODEL}, used {list(usage)}", cost, usage)
    try:
        choice = DeskChoice.model_validate(envelope.get("structured_output"))
    except ValidationError as err:
        first = err.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "root"
        return _review(
            f"schema: {err.error_count()} errors, first {where} {first['type']}", cost, usage
        )
    return DeskResult(choice=choice, review_reason=None, cost_usd=cost, model_usage=usage)


def decide(bars: Bars) -> DeskResult:
    with tempfile.TemporaryDirectory(prefix="traderbot-desk-") as cwd:
        try:
            proc = subprocess.run(
                argv(),
                input=prompt(bars),
                capture_output=True,
                text=True,
                cwd=cwd,
                env=env(),
                timeout=TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _review(f"timeout after {TIMEOUT_S}s", CALL_CAP_USD)
    return parse(proc.stdout, proc.returncode)
