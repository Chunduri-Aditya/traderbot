# ARCHITECTURE

_How it is built. Committed. Update in the same commit as any change to the flow, layout, or stack._

## Flow
- propose: `keychain` keys, then `data` (calendar sets `as_of`, SIP daily bars), then `desk` (one clean claude call, schema only), then `proposal` (code sizes qty and limit, sha256 digest), then `gate` (pure verdict), then `ledger` (card row).
- approve: `ledger` card, sha256 of the stored text checked against its key, then `broker` lookup by client id (return it if found), else `gate` rerun on live account state against the signed `requested_symbol`, then a DAY limit buy on the paper host.

## Stack
- Python 3.13 (`>=3.12`), uv with `uv_build`: Aditya's default stack.
- `alpaca-py==0.44.0`: the only runtime dependency; trading, calendar and market data in one SDK (brings pydantic, used for the desk schema).
- sqlite (stdlib) under `TRADERBOT_HOME`, default `~/.traderbot`: one local file, no server.
- `claude` CLI headless on a clean profile for the desk: no tools, no MCP, env allowlist, pinned `claude-sonnet-5-5`.

## Layout
- `src/traderbot/cli.py`: argparse commands and their readable output.
- `src/traderbot/gate.py`: the only place a rule lives; `Limits` holds Aditya's numbers until Phase 3.
- `src/traderbot/broker.py`: every trading client passes the paper host check here.
- `src/traderbot/{cache,fence,scoring,backtest,fundamentals,live_gate}.py`: loud stubs until their phase.
- `tests/test_smoke.py`: the live path in two halves, the real desk and a code built buy through approve (marked `live`, deselected by default); `tests/test_broker.py`: the paper host check.

## Boundaries
- Alpaca paper trading (`paper-api.alpaca.markets`) and market data (`data.alpaca.markets`); keys from the Keychain item `traderbot-alpaca-paper`, never in env, files or logs.
- The desk's output is untrusted: it reaches the order only as schema fields, and code sets qty and price; the gate rechecks everything before any submit.
- Approval is Aditya's: project deny rules keep the agent off `approve` (Phase 2 adds the `/dev/tty` read).

## Provenance
Ideas rebuilt in original code; nothing is pasted from `repos/` (OpenBB `develop` is AGPL 3.0).

| Idea | Source | License | Phase |
|---|---|---|---|
| Clean headless claude profile: flags, env allowlist, schema stop reasons, pinned model as the only `modelUsage` key | `/Users/chunduri/Desktop/twin/src/twin/adapters.py:25,46-60,67-70,200-205` | Aditya's own | 1 |
| `live` pytest marker deselected by default | `/Users/chunduri/Desktop/twin/pyproject.toml` | Aditya's own | 1 |
| Unparseable model output becomes REVIEW, never a default hold | tradingagents `agents/rating.py:27-31,49-88` (`audits/tradingagents.md:71`) | Apache 2.0 | 1 |
| Every price carries `as_of`, and the order path refuses stale data | osiris `src/app/api/markets/route.ts:177` and tradingview-mcp `quote_stale` (`audits/COMPARISON.md`, pattern 1) | MIT | 1 |
