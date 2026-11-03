"""Offline tests never reach a paid or live service, and never touch ~/.traderbot.

For every test not marked `live`: the claude runner, the Keychain read, every HTTP request
through requests (Alpaca), urllib's opener and the EDGAR reader (data.sec.gov) are tripwires
derived from BaseException (so `except Exception` and the retry loop cannot swallow them), and
TRADERBOT_HOME points at the test's tmp_path.
"""

import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import requests
from support import Tripwire

from traderbot import desk, fundamentals, keychain, ledger

REAL_RUN_CLAUDE = desk._run_claude


def _tripwire(what: str) -> Callable[..., Any]:
    def tripped(*args: Any, **kwargs: Any) -> Any:
        raise Tripwire(f"an offline test reached {what}")

    return tripped


@pytest.fixture(autouse=True)
def offline(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setenv(ledger.HOME_ENV, str(tmp_path))
    monkeypatch.setattr(desk, "_run_claude", _tripwire("the claude CLI"))
    monkeypatch.setattr(keychain, "_read", _tripwire("the Keychain"))
    monkeypatch.setattr(requests.Session, "request", _tripwire("an HTTP host"))
    monkeypatch.setattr(urllib.request, "urlopen", _tripwire("urllib's opener"))
    monkeypatch.setattr(fundamentals, "_http_get_json", _tripwire("data.sec.gov"))


@pytest.fixture
def real_run_claude() -> Callable[..., tuple[str, int, bool]]:
    """The unpatched runner, for the one test that drives it with a fake CLI script."""
    return REAL_RUN_CLAUDE
