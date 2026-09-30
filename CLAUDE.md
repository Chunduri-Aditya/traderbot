# traderbot

A personal paper trading bot: a claude desk proposes, a deterministic gate checks, Aditya approves each order. Build state lives in `MEMORY.md`; phases in `PHASES.md`.

@RULES.md

## Commands
- `uv sync`: install into `.venv`.
- `uv run pytest`: offline tests, free (`live` is deselected by default in `pyproject.toml`).
- `uv run pytest -m live tests/test_smoke.py`: the live path on a tmp ledger in two halves; one desk call (about $0.01) and one paper order, cancelled in teardown while open.
- `./scripts/check.sh`: ruff, ruff format, mypy strict and pytest, each with its own labeled exit code.
- `uv run python scripts/mutate.py --scratch <dir>`: breaks each gate rule in turn; every row must print RED.
- `uv run traderbot propose SYMBOL`, `status`, `reject PREFIX`, `off`, `on`, each with `--json`: the real ledger at `~/.traderbot/ledger.sqlite`.
- `uv run traderbot approve` and `uv run traderbot reset-breaker`: Aditya only, in Terminal.app; both read from `/dev/tty`, and the deny rules block `approve` for the agent.
