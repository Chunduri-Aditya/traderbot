# Repo audit comparison

Six repos, static review only (nothing installed or run). Per repo detail and file:line citations live in the sibling reports. Claims marked (checked) were read back from the clone in the main session; the rest come from the agent reports.

## Verdicts

| Repo | What it is | License | Call for traderbot | Biggest caveat |
|---|---|---|---|---|
| OpenBB | Python data layer, 32 providers behind one typed query model; Python, REST, CLI, MCP | AGPL 3.0 on `develop`, Apache 2.0 on `v5` (checked) | Use as research and backtest data layer, small provider subset | Work moved to `v5` (213 commits ahead, active 2026-09-28, checked), which drops 15 providers incl. yfinance, FMP, Tiingo |
| tradingagents | LangGraph pipeline of 12 LLM roles, ticker plus date to a 5 tier rating; no orders | Apache 2.0 | Rebuild 3 ideas, do not adopt | Its reported returns validate nothing: fundamentals dated by period not filing (checked), back adjusted prices, no costs, no memorization guard |
| paperclip | Node and React control plane for "companies" of coding agents | MIT | No as orchestration layer; rebuild about 8 patterns | Agents default to `--dangerously-skip-permissions` (checked) and inherit server env, so broker keys cannot be isolated |
| tradingview-mcp | Python MCP server, 39 read only tools over TradingView scanner, Yahoo, Marketaux, CoinGecko | MIT | Rebuild patterns, do not use or fork | Undocumented TradingView endpoint with forged Origin headers; ToS and IP ban risk |
| awesome-systematic-trading | Link list (299 rows) and vendor sales funnel | None | Index only | Strategy table is vendor marketing; 5 of 7 crypto rows claim 22 to 35 test years (checked) |
| osiris | OSINT map dashboard on Next.js 16 | MIT | Do not build on | Not a trading bot; forecaster engine ships as 15 `.pyc` files, no source (checked) |

## Security findings worth remembering

| Repo | Severity | Finding |
|---|---|---|
| tradingagents | High | Reddit, StockTwits and news text go verbatim into the sentiment analyst system prompt inside forgeable tags; decisions feed later prompts across tickers through a log whose separator the model can emit (checked) |
| paperclip | High | `local_trusted` mode makes any local caller without a token an instance admin (checked) |
| osiris | High | CCTV proxy sets `rejectUnauthorized = false` and follows redirects recursively (checked) |
| tradingview-mcp | High | News text reaches the model unmarked; Docker path binds streamable HTTP on `0.0.0.0` with no auth (checked) |
| tradingview-mcp | Secret | Webshare proxy password in history at `25a52af`; HEAD holds a placeholder with a different hash (checked); liveness unknown |
| OpenBB | Medium | API keys in plaintext JSON; REST and MCP servers default to no auth and CORS `*` |
| osiris | Medium | Gemini quota spendable by anyone via a forgeable client IP header |

## Patterns across repos

These recur, so they read as requirements for traderbot rather than one repo's bugs.

1. Stale data served as fresh. osiris (Yahoo panel) and tradingview-mcp (6 hour cache labelled `"real-time"`). Every quote needs an `as_of` and a stale flag the order path checks.
2. Untrusted text in prompts that can act. tradingagents and tradingview-mcp both pass scraped text to the model unmarked, and tradingagents loops the output back into memory. Anything scraped is data in a quoted channel, and no model output that saw it reaches orders without a deterministic gate.
3. Local servers with no auth. OpenBB, tradingview-mcp (Docker) and paperclip all default open. Bind 127.0.0.1 and require a token even locally.
4. Lookahead through data, not code. tradingagents (period dated fundamentals, back adjusted prices) and the awesome list (impossible test windows). Point in time data with filing dates, or no backtest claims.
5. Controls that are displayed but not enforced mid run. paperclip budgets stop only after cost is recorded, using agent reported cost. Risk limits belong in the execution layer, checked before each order.

## Worth rebuilding in our own code

| Idea | Source | Note |
|---|---|---|
| Run date from graph state, clamp any date the model requests | tradingagents | Kills tool level lookahead |
| Score a decision only after its holding window trades; a lesson reaches a run only if known by that run's date | tradingagents | Point in time memory |
| Bull, bear, judge, three stance risk review; unparseable output becomes a REVIEW flag, never a default Hold | tradingagents | |
| TTL cache with request dedup, plus `as_of` and stale flag | osiris `sourceCache.ts` | |
| `safeFetch` SSRF guard | osiris `ssrf-guard.ts` | |
| Brier ledger scored against settled outcomes | osiris PYTHIA idea | Not an LLM grading its own archive |
| Typed provider fetchers behind one query model | OpenBB | Or depend on it for research data |

## Gap

None of the six is an execution or backtest engine. The awesome list's top engine candidates (nautilus_trader, QuantConnect LEAN, pysystemtrade, PyBroker, ccxt) are unaudited.
