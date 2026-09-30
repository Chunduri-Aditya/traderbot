"""The desk's process handling and its sell answers, offline. The one real process test drives
the unpatched runner with a fake CLI script, never claude."""

import json
import os
import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from traderbot import desk
from traderbot.data import Bars

BARS = Bars(
    symbol="SPY",
    as_of=date(2026, 9, 29),
    bar_date=date(2026, 9, 29),
    closes=((date(2026, 9, 29), Decimal("764.20")),),
)
Runner = Callable[..., tuple[str, int, bool]]


def envelope(**structured: object) -> str:
    return json.dumps(
        {
            "is_error": False,
            "stop_reason": "tool_use",
            "total_cost_usd": 0.01,
            "modelUsage": {desk.MODEL: {}},
            "structured_output": structured,
        }
    )


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_timeout_kills_the_whole_process_group(tmp_path: Path, real_run_claude: Runner) -> None:
    pidfile = tmp_path / "grandchild.pid"
    script = tmp_path / "fake-claude"
    script.write_text(f'#!/bin/sh\nsleep 60 &\necho $! > "{pidfile}"\nwait\n')
    script.chmod(0o700)
    env = {"PATH": os.environ["PATH"]}
    _, _, timed_out = real_run_claude([str(script)], "", str(tmp_path), env, 1)
    assert timed_out
    grandchild = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(grandchild), "the CLI's own child outlived the timeout"


def test_a_fast_process_returns_its_output(tmp_path: Path, real_run_claude: Runner) -> None:
    script = tmp_path / "fake-claude"
    script.write_text("#!/bin/sh\ncat\n")
    script.chmod(0o700)
    env = {"PATH": os.environ["PATH"]}
    assert real_run_claude([str(script)], "hello", str(tmp_path), env, 5) == ("hello", 0, False)


def test_timeout_is_review_charged_at_the_call_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desk, "_run_claude", lambda *args: ("", -9, True))
    result = desk.decide(BARS)
    assert result.choice is None and result.cost_usd == desk.CALL_CAP_USD
    assert result.review_reason == f"timeout after {desk.TIMEOUT_S}s"


def test_sell_is_a_valid_answer() -> None:
    choice = desk.parse(envelope(symbol="SPY", side="sell", rationale="r", confidence=0.6), 0)
    assert choice.choice is not None and choice.choice.side == "sell"


def test_short_or_qty_fields_are_refused_by_the_schema() -> None:
    for extra in ({"side": "short"}, {"side": "buy", "qty": 100}):
        fields = {"symbol": "SPY", "rationale": "r", "confidence": 0.6} | extra
        result = desk.parse(envelope(**fields), 0)
        assert result.choice is None and str(result.review_reason).startswith("schema:"), extra


def test_prompt_tells_the_desk_the_position() -> None:
    assert "held now: 7" in desk.prompt(BARS, 7)
