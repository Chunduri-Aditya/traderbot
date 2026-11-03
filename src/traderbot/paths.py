"""Where traderbot keeps its files: TRADERBOT_HOME, default ~/.traderbot, created at 0700.

Its own module so the ledger and the cache share one rule without importing each other.
"""

import os
from pathlib import Path

HOME_ENV = "TRADERBOT_HOME"
DEFAULT_HOME = "~/.traderbot"


def home() -> Path:
    root = Path(os.environ.get(HOME_ENV) or DEFAULT_HOME).expanduser()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root
