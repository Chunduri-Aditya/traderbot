# DESIGN

_Keeps the look and output consistent across generated code. Committed._

## Output convention (CLI, no UI)
- A readable view by default: one header line of key facts (card prefix, symbol, side, qty at limit, verdict, cost), the content (rationale), then where the ledger lives.
- Machine JSON behind `--json` on every command, ASCII only.
- Model and user text passes through `cli.shown` before it reaches the terminal, so control characters print escaped and never act.
- Refusals print one `refused: <reason>` line on stderr and exit 1.

## Direction
- No UI. The output convention above applies; a notification in Phase 6 lists new cards only.
