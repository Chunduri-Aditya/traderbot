# traderbot

A personal paper trading bot: a claude desk proposes, a deterministic gate checks, Aditya approves each order. Build state lives in `MEMORY.md`; phases in `PHASES.md`.

@RULES.md

## Commands
- `uv sync`: install into `.venv`.
- `uv run pytest`: offline tests, free (`live` is deselected by default in `pyproject.toml`).
- `uv run pytest -m live tests/test_smoke.py`: the live path on a tmp ledger in two halves; one desk call (about $0.01) and one paper order, cancelled in teardown while open.
- `uv run traderbot propose SYMBOL` and `uv run traderbot status`: the real ledger at `~/.traderbot/ledger.sqlite`.
- `uv run traderbot approve PREFIX`: Aditya only, in Terminal.app; the project deny rules block it for the agent.
