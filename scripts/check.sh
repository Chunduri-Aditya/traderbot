#!/usr/bin/env bash
# The objective gate: every step runs, each prints its own exit code, and any nonzero fails.
cd "$(dirname "$0")/.." || exit 2
uv run ruff check .; ruff=$?
echo "ruff=$ruff"
uv run ruff format --check .; fmt=$?
echo "fmt=$fmt"
uv run mypy src tests; mypy=$?
echo "mypy=$mypy"
uv run pytest; pytest=$?
echo "pytest=$pytest"
[ "$ruff" -eq 0 ] && [ "$fmt" -eq 0 ] && [ "$mypy" -eq 0 ] && [ "$pytest" -eq 0 ]
