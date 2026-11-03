# ARCHITECTURE

_How it is built. Committed. Update in the same commit as any change to the flow, layout, or stack._

## Flow
- propose: kill switch check, `keychain` keys, `data` (calendar sets `as_of`, SIP daily RAW bars, both through `cache`; stale refuses), account snapshot (a tripped breaker refuses), a spend reservation of the $0.25 call cap, `desk` (one clean claude call in its own process group, schema only), the cost settled, `proposal` (code sizes qty and limit, buy or sell, sha256 digest, expiry at the next session close), `gate` (pure verdict), `ledger` (card row and an event).
- approve: the prefix is typed at `/dev/tty` (no terminal, no approval), then the `ledger` card, sha256 of the stored text checked against its key, a card denied at propose or rejected refused, `broker` lookup by client id (return it if found), else `gate` rerun on live account state against the signed `requested_symbol`, the card's order slot claimed in one transaction (one claim per card; the daily cap counts claims), the clock read once more, then a DAY limit order on the paper host unless the kill switch file exists; the approve decision is recorded only once an order exists, and a submit that raises leaves an unconfirmed claim that the next approve, reject or status looks up by client id.

## Stack
- Python 3.13 (`>=3.12`), uv with `uv_build`: Aditya's default stack.
- `alpaca-py==0.44.0`: the only runtime dependency; trading, calendar and market data in one SDK (brings pydantic, used for the desk schema).
- sqlite (stdlib) under `TRADERBOT_HOME`, default `~/.traderbot`: the ledger and a separate read through cache (`cache.sqlite`), no server.
- `urllib.request` (stdlib) for SEC EDGAR companyfacts on `data.sec.gov`, with a fixed `User-Agent`.
- `claude` CLI headless on a clean profile for the desk: no tools, no MCP, env allowlist, pinned `claude-sonnet-5-5`.

## Layout
- `src/traderbot/cli.py`: argparse commands and their readable output.
- `src/traderbot/gate.py`: the only place a rule lives (fourteen reason codes); `Limits` holds Aditya's numbers until Phase 3.
- `src/traderbot/ledger.py`: cards, orders, decisions, spend reservations, append only events, equity marks, settlements (written from Phase 4), the breaker latch, and the `OFF` kill switch file.
- `scripts/check.sh`: ruff, ruff format, mypy strict and pytest, one labeled exit code each; `scripts/mutate.py`: breaks each gate rule and requires its named test to fail.
- `src/traderbot/broker.py`: every trading client passes the paper host check here.
- `src/traderbot/paths.py`: `TRADERBOT_HOME` at 0700, shared by the ledger and the cache.
- `src/traderbot/cache.py`: `get_or_fetch` (canonical JSON with its sha256; a corrupt, expired or future stamped row is refetched, never served) and `retry_with_backoff` (3 attempts, transient errors only: timeouts, dropped connections, HTTP 429 and 5xx).
- `src/traderbot/data.py`: the calendar (cached a day) and SIP daily closes. RAW closes size orders and feed the gate; `Adjustment.ALL` closes feed settlement, desk context and backtests, cached under the ET fetch date because a later split or dividend rewrites them. A window whose `as_of` bar is not served whole is never cached.
- `src/traderbot/fundamentals.py`: EDGAR companyfacts; `point_in_time_facts` keeps only facts with `filed <= as_of` and `end <= as_of`, the latest period per field, `Decimal` values. `scripts/pit_check.py` proves it on a live fetch.
- `src/traderbot/{fence,scoring,backtest,live_gate}.py`: loud stubs until their phase.
- `tests/test_smoke.py`: the live path in two halves, the real desk and a code built buy through approve (marked `live`, deselected by default). `tests/conftest.py` makes claude, the Keychain, every `requests` call, `urllib.request.urlopen` and the EDGAR reader tripwires in offline tests and points `TRADERBOT_HOME` at `tmp_path`.

## Boundaries
- SEC EDGAR (`data.sec.gov`), read only, no key; its text is untrusted.
- Alpaca paper trading (`paper-api.alpaca.markets`) and market data (`data.alpaca.markets`); keys from the Keychain item `traderbot-alpaca-paper`, never in env, files or logs.
- The desk's output is untrusted: it reaches the order only as schema fields, and code sets qty and price; the gate rechecks everything before any submit.
- Approval is Aditya's: `approve` and `reset-breaker` read from `/dev/tty`, which an agent shell does not have (Errno 6), and project deny rules keep the agent off `approve` and both Keychain read paths. A human who runs code as Aditya can bypass anything in process.

## Provenance
Ideas rebuilt in original code; nothing is pasted from `repos/` (OpenBB `develop` is AGPL 3.0).

| Idea | Source | License | Phase |
|---|---|---|---|
| Clean headless claude profile: flags, env allowlist, schema stop reasons, pinned model as the only `modelUsage` key | `/Users/chunduri/Desktop/twin/src/twin/adapters.py:25,46-60,67-70,200-205` | Aditya's own | 1 |
| `live` pytest marker deselected by default | `/Users/chunduri/Desktop/twin/pyproject.toml` | Aditya's own | 1 |
| Unparseable model output becomes REVIEW, never a default hold | tradingagents `agents/rating.py:27-31,49-88` (`audits/tradingagents.md:71`) | Apache 2.0 | 1 |
| Every price carries `as_of`, and the order path refuses stale data | osiris `src/app/api/markets/route.ts:177` and tradingview-mcp `quote_stale` (`audits/COMPARISON.md`, pattern 1) | MIT | 1 |
| Reserve spend before a call; a dead reservation or a missing cost is charged in full | `/Users/chunduri/Desktop/twin/src/twin/spend.py:46-101` (rebuilt on a sqlite transaction) | Aditya's own | 2 |
| Typed digest prefix, compared in constant time | `/Users/chunduri/Desktop/twin/src/twin/approval.py:33,165-168` (the TTY read is new) | Aditya's own | 2 |
| Escape every character that can drive a terminal or render as nothing, Hangul fillers included | `/Users/chunduri/Desktop/twin/src/twin/render.py:17-28` | Aditya's own | 2 |
| Offline tests make paid calls tripwires | `/Users/chunduri/Desktop/twin/tests/conftest.py:186-190` | Aditya's own | 2 |
| Redact logged details by key name at any depth | paperclip `server/src/redaction.ts:881`, `server/src/services/activity-log.ts:75-87` | MIT | 2 |
| Circuit breaker on consecutive losses, halting on an invalid starting value | ECC `skills/llm-trading-agent-security/SKILL.md:92-110` (its regex injection denylist is not adopted) | MIT | 2 |
| A fundamental counts only once filed: `filed <= as_of` on every EDGAR fact (here the latest filing by `as_of` wins, so a restatement shows from its own filing date) | tradingagents `dataflows/vendors/sec_edgar.py:147-185` (`audits/tradingagents.md:78`) | Apache 2.0 | 3 |
| TTL cache that never serves a partial or stale window as fresh | osiris `sourceCache.ts` (`audits/COMPARISON.md`, worth rebuilding) | MIT | 3 |
