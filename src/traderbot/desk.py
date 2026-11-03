"""The desk: one headless claude call on a clean profile, answering through a schema.

No tools, no MCP, no settings but the project's (and the cwd is a fresh temp dir), an env
allowlist, the prompt on stdin, a pinned model that must be the only one in `modelUsage`. Any
failure (timeout, bad JSON, error envelope, wrong stop such as a budget stop, wrong model,
schema, or a rationale that echoes the fence nonce or a fence tag) is REVIEW, never hold.

The prompt walks seven roles in that one call (Macro, Technical, Fundamentals, Flow/Sentiment,
Tail-Risk, a Devil's Advocate Bear, and the Portfolio Manager Arbiter who answers), so the
$0.25 cap and the clean profile hold for the whole desk. Bars are written by code; EDGAR text
and the desk's own track record reach the prompt only inside a nonce fence (`fence.py`), with a
fresh nonce per call. The profile follows twin's clean profile (/Users/chunduri/Desktop/twin/src/twin/adapters.py
flags at 46-60, env at 25 and 200-205, stop reasons at 67-70), rebuilt here.
"""

import contextlib
import json
import os
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from traderbot import fence
from traderbot.data import Bars
from traderbot.fundamentals import FundamentalSnapshot
from traderbot.proposal import DeskChoice
from traderbot.scoring import ScoreReport

CLAUDE_BIN = "claude"
MODEL = "claude-sonnet-5-5"
EFFORT = "low"
CALL_CAP_USD = Decimal("0.25")
TIMEOUT_S = 300
REAP_S = 5  # after the group kill, how long to wait for the pipes to close
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


ROLES = (
    ("Macro", "the market regime and how this symbol tends to move with SPY"),
    ("Technical", "trend, momentum and mean reversion in the closes"),
    ("Fundamentals", "the EDGAR facts if any are given, and what they mean over 5 sessions"),
    ("Flow/Sentiment", "what the price path suggests about positioning; no news is given"),
    ("Tail-Risk", "what could make this trade lose badly within 5 sessions"),
    ("Devil's Advocate Bear", "the strongest case against the view the others lean to"),
    ("Portfolio Manager Arbiter", "weighs the six views and gives the final side and confidence"),
)


@dataclass(frozen=True)
class DeskContext:
    fundamentals: FundamentalSnapshot | None = None
    track_record: ScoreReport | None = None


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


def _fundamentals_block(snapshot: FundamentalSnapshot, nonce: str) -> str:
    lines = [fence.wrap_untrusted("edgar_entity", snapshot.entity_name, nonce)]
    for fact in snapshot.facts:
        period = f"{fact.start} to {fact.end}" if fact.start else f"at {fact.end}"
        text = (
            f"{fact.field} {fact.value} for {period}, filed {fact.filed}"
            f" in {fact.form} {fact.fiscal_period}"
        )
        lines.append(fence.wrap_untrusted("edgar_fact", text, nonce))
    if snapshot.missing:
        lines.append(f"Not reported by {snapshot.as_of}: {', '.join(snapshot.missing)}.")
    return "\n".join(lines)


def _track_block(report: ScoreReport, nonce: str) -> str:
    text = (
        f"{report.settled} settled desk calls; win rate {report.win_rate}; mean alpha against"
        f" SPY {report.mean_alpha_bps} bps (95% CI {report.alpha_ci}); mean Brier"
        f" {report.mean_brier}"
    )
    return fence.wrap_untrusted("track_record", text, nonce)


def prompt(
    bars: Bars, position_qty: int = 0, context: DeskContext | None = None, nonce: str = ""
) -> str:
    """The desk prompt. `nonce` fences untrusted text; decide() draws a fresh one per call."""
    nonce = nonce or fence.new_nonce()
    context = context or DeskContext()
    rows = "\n".join(f"{day.isoformat()} {close}" for day, close in bars.closes)
    roles = "\n".join(f"{i}. {name}: {brief}." for i, (name, brief) in enumerate(ROLES, 1))
    parts = [
        (
            "You are the analyst desk of a personal paper trading bot. Decide whether to buy "
            f"{bars.symbol} with a DAY limit order just above the last close, to hold for about "
            "5 sessions; to sell shares already held with a DAY limit order just below it; or "
            "to hold off. Code sets the quantity and the price; you choose only the side."
        ),
        fence.preamble(nonce),
        f"Work through seven roles in order, each in a sentence or two of reasoning:\n{roles}",
        f"Shares of {bars.symbol} held now: {position_qty}. Selling more than that is refused.",
        (
            f"Daily raw closes for {bars.symbol}, oldest first; the last complete session is "
            f"{bars.as_of.isoformat()}:\n{rows}"
        ),
    ]
    if context.fundamentals is not None:
        parts.append(
            f"SEC EDGAR facts filed on or before {context.fundamentals.as_of}:\n"
            + _fundamentals_block(context.fundamentals, nonce)
        )
    if context.track_record is not None and context.track_record.settled:
        parts.append(
            "This desk's settled record, for calibrating confidence:\n"
            + _track_block(context.track_record, nonce)
        )
    parts.append(
        f"Answer through the schema as the Portfolio Manager Arbiter: symbol (exactly "
        f"{bars.symbol}), side (buy, sell or hold), rationale (at most 900 characters: the "
        "decision and the role views that settled it), confidence (0 to 1, your probability "
        "that the decision is right)."
    )
    return "\n\n".join(parts)


def _review(reason: str, cost: Decimal, usage: tuple[str, ...] = ()) -> DeskResult:
    return DeskResult(choice=None, review_reason=reason, cost_usd=cost, model_usage=usage)


def _cost(envelope: dict[str, Any]) -> Decimal:
    """A missing or non numeric cost is charged at the call cap."""
    raw = envelope.get("total_cost_usd")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
        return CALL_CAP_USD
    return Decimal(str(raw))


def parse(stdout: str, returncode: int, nonce: str | None = None) -> DeskResult:
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
    if nonce is not None and not fence.verify_no_fence_leak(choice.rationale, nonce):
        return _review("fence leak: the rationale echoes the nonce or a fence tag", cost, usage)
    return DeskResult(choice=choice, review_reason=None, cost_usd=cost, model_usage=usage)


def _run_claude(
    command: list[str], stdin: str, cwd: str, environ: dict[str, str], timeout_s: int
) -> tuple[str, int, bool]:
    """(stdout, exit code, timed out). The child leads its own process group, and a timeout kills
    the whole group, so nothing it started outlives the call."""
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=cwd,
        env=environ,
        start_new_session=True,
    )
    try:
        stdout, _ = proc.communicate(stdin, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        try:
            proc.communicate(timeout=REAP_S)
        except subprocess.TimeoutExpired:
            proc.kill()
        return "", -signal.SIGKILL, True
    return stdout, proc.returncode, False


def decide(
    bars: Bars, position_qty: int = 0, context: DeskContext | None = None, nonce: str = ""
) -> DeskResult:
    nonce = nonce or fence.new_nonce()
    text = prompt(bars, position_qty, context, nonce)
    with tempfile.TemporaryDirectory(prefix="traderbot-desk-") as cwd:
        stdout, code, timed_out = _run_claude(argv(), text, cwd, env(), TIMEOUT_S)
    if timed_out:
        return _review(f"timeout after {TIMEOUT_S}s", CALL_CAP_USD)
    return parse(stdout, code, nonce)
