# Audit: `atilaahmettaner/tradingview-mcp`

Audited 2026-09-28 against commit `a1e54b0` (HEAD of `main`, 2026-09-01). Static review only: nothing in the repo was installed, run, or tested. No network access. Every count below came from a shell command or a Python AST script over the tracked files.

**Verdict**
1. A Python FastMCP server (39 read only tools) that pulls TradingView data from the undocumented, unauthenticated endpoints behind TradingView's own web app, plus Yahoo Finance, Marketaux news and CoinGecko, and wraps them in screeners, heuristic "trade setups" and a small long only backtester; it is also the open source front for a paid hosted service.
2. Not worth using as is inside traderbot: no order or history API worth building on, TradingView data can be served up to 6 hours stale while labeled `"real-time"`, and news text reaches the model unmarked. Worth mining for a few patterns (retry and cooldown layer, typed error envelopes, async offload, annotation test), rebuilt in our own code.
3. Biggest caveat: the data source is TradingView's undocumented scanner endpoint with spoofed browser headers, and Yahoo's unofficial API behind optional rotating residential proxies meant to get past rate limits; terms of service risk and breakage risk sit on whoever runs it.

## Facts

| Item | Value | Source |
|---|---|---|
| Kind | MCP server, Python, FastMCP from the official `mcp` SDK | `src/tradingview_mcp/server.py:26`, `:114` |
| Version | 0.9.0 | `pyproject.toml:3`, `server.json:9` |
| License | MIT, copyright 2025 Ahmet Taner Atila | `LICENSE:1` to `:3` |
| HEAD | `a1e54b0`, 2026-09-01, merge of PR #94 | `git log -1` |
| Commits | 150 on `main`, 166 across all refs, 16 remote branches | `git rev-list --count` |
| Authors | 21 distinct names, 23 emails; owner 98 commits across 3 identities; next is Galal Shaban with 20 | `git log --format='%an <%ae>'` |
| Explicit AI co author trailers | 22 of 150 commits (14.7%), all Claude models | `git log -i -E --grep='co-authored-by:.*claude'` |
| MCP tools | 39 (9 declared async, 30 sync wrapped into threads at import), 1 resource, 1 custom HTTP route | AST scan of `server.py`; `server.py:1180`, `:1203`, `:1233` to `:1252` |
| Transports | `stdio` (default) and `streamable-http`; no SSE option | `server.py:1257` to `:1263` |
| Network auth | None in the repo | `git grep` for auth, middleware, quota, tenant: 0 hits in `src` |
| Python source | 26 files, 11,679 lines | `wc -l` |
| Tests | 105 test functions in 30 files: 97 unit (CI), 8 stress (real network, opt in) | `grep -cE 'def test_'` |
| Locked key deps | `mcp` 1.29.0, `tradingview-screener` 3.0.0, `tradingview-ta` 3.3.0, `requests` 2.32.4, `httpx` 0.28.1, `pandas` 2.3.1 | `uv.lock` |
| Bundled symbol lists | 29 files, 19,396 lines; 10 of 29 last touched 2025-08-15 | `wc -l`, `git log -1 -- <file>` |
| Hosted twin | `https://mcp.cryptosieve.com/mcp`, $9 and $29 per month | `server.json:11` to `:16`, `README.md:65`, `:133` to `:140` |

## 1. What it is

An MCP server that answers market questions for LLM clients. The server layer (`server.py`) validates a few parameters and delegates to service modules in `src/tradingview_mcp/core/services/`. Tool families, by handler name:

| Family | Tools | Count |
|---|---|---|
| Crypto and stock screeners (TradingView) | `top_gainers`, `top_losers`, `bollinger_scan`, `rating_filter`, `consecutive_candles_scan`, `advanced_candle_pattern`, `volume_breakout_scanner`, `smart_volume_scanner` | 8 |
| Single symbol analysis (TradingView) | `coin_analysis`, `volume_confirmation_analysis`, `multi_agent_analysis`, `multi_timeframe_analysis` | 4 |
| Egypt (EGX) | `egx_market_overview`, `egx_sector_scan`, `egx_sector_scanner`, `egx_index_analysis`, `egx_stock_screener`, `egx_trade_plan`, `egx_fibonacci_retracement`, `egx_smart_money_scanner` | 8 |
| Smart money heuristics (Yahoo OHLCV) | `smart_money_analysis` | 1 |
| News and sentiment (Marketaux) | `market_sentiment`, `financial_news`, `combined_analysis` | 3 |
| Backtests (Yahoo OHLCV) | `backtest_strategy`, `compare_strategies`, `walk_forward_backtest_strategy` | 3 |
| Yahoo quotes and options | `yahoo_price`, `market_snapshot`, `stock_extended_hours`, `stock_options_chain`, `stock_options_unusual_activity` | 5 |
| CoinGecko | `bitcoin_market_pulse` | 1 |
| Futures (TradingView scanner) | `futures_market_overview`, `futures_top_movers`, `futures_category_snapshot`, `futures_watchlist` | 4 |
| Global stock screener (TradingView scanner) | `stock_screener`, `stock_prices` | 2 |

Maturity: version 0.9.0 with a changelog back to 0.1.0 (`CHANGELOG.md:7`, `:261`); ten dated releases between 2026-03-24 and 2026-08-26. A unit suite runs in CI on Python 3.10 to 3.13 (`.github/workflows/test.yml:24`, `:38`). No linter or type checker is configured (`grep` for ruff, mypy, flake8, pyright in `pyproject.toml` and workflows: 0 hits).

Commercial shape: the README sells a hosted version of the same tools at `pro.cryptosieve.com` with quotas (2,500 calls per month and 60 per minute on Pro) (`README.md:65`, `:133` to `:140`). The hosted service runs code that is not all in this repo: the README says "The hosted server now draws" with MCP Apps charts (`README.md:12`), and `git grep` for `ui://`, `text/html`, `resourceUri` in `src` returns 0 hits. The advertised quotas also have no implementation here.

OpenClaw: a third party chat gateway (Telegram, WhatsApp, Discord) that runs an LLM agent with a bash tool. The repo ships two files for it. `openclaw/trading.py` is a CLI that imports the service modules directly, without MCP, and exposes 6 commands (`price`, `snapshot`, `backtest`, `compare`, `walkforward`, `sentiment`, `openclaw/trading.py:61` to `:86`). `openclaw/SKILL.md` is an instruction file for the OpenClaw agent. `OPENCLAW.md` is the setup guide. OpenClaw is an add on, not part of the MCP server.

## 2. Data sources

| Source | Endpoint | How | Used by | Evidence |
|---|---|---|---|---|
| TradingView scanner | `scanner.tradingview.com/{market}/scan` | `tradingview-ta` (TradingView computed indicator values per symbol) and `tradingview-screener` (scanner queries); one direct `requests.post` for the ATR column | every TradingView tool | `screener_provider.py:405` to `:435`, `:531` to `:537`; `stock_screener_service.py:24`; `futures_service.py:11` |
| TradingView symbol search | `symbol-search.tradingview.com/symbol_search/v3/` | urllib with a browser User Agent plus `Origin` and `Referer` set to `https://www.tradingview.com` | not found error enrichment | `screener_service.py:572`, `:613` to `:619` |
| Yahoo Finance | `query1.finance.yahoo.com/v8/finance/chart`, `query2.finance.yahoo.com/v7/finance/options` | unofficial JSON API; options path does the `fc.yahoo.com` cookie drop plus `getcrumb` handshake with a Chrome User Agent | quotes, backtests, smart money, options, extended hours | `yahoo_finance_service.py:38`; `backtest_service.py:30`; `options_service.py:43` to `:46`, `:77` to `:110` |
| Webshare rotating residential proxy | `p.webshare.io:80` | optional, from env; Yahoo requests fall back to it | Yahoo paths | `proxy_manager.py:55` to `:83`; `backtest_service.py:73` to `:79` |
| Marketaux | `api.marketaux.com/v1/news/all` | licensed API, token from env, 90 requests per day budget, 4 hour cache | news and sentiment | `marketaux_service.py:33` to `:35`, `:54` to `:59`, `:79` to `:83` |
| CoinGecko | `api.coingecko.com/api/v3/global`, `/simple/price` | public API, no key | `bitcoin_market_pulse` | `bitcoin_market_service.py:26` to `:30` |

No TradingView login, session cookie, or account is used: `_scan_with_retry` passes `cookies=None` by default (`screener_provider.py:340`, `:357`) and no call site passes cookies (`git grep 'cookies='` in `src`: only the pass through). No websocket feed and no browser automation.

Terms of service risk:
* TradingView publishes no public API for the scanner. These endpoints serve TradingView's own web app. The symbol search call forges `Origin` and `Referer` because `The endpoint 403s bare python UAs; browser-shaped headers required` (`screener_service.py:615` to `:618`). The README says the server does "not log into, scrape, or automate a TradingView session" (`README.md:40`); the no login part is accurate, the no scrape part does not match calling private web app endpoints with forged browser headers. TradingView's actual terms text was not checked (no network), so the exact clause is Unverified; the practical risk is IP blocking and silent schema changes, and the hosted resale adds commercial exposure for the operator.
* Yahoo: the chart and options endpoints are unofficial; the crumb flow exists because Yahoo gated them (`options_service.py:47` to `:50`). The proxy exists to get around limits: `Enables access to Yahoo Finance and Xueqiu behind rate-limits` (`.env.example:21`), with up to 250 sticky proxy sessions (`proxy_manager.py:63`, `:78`).
* Marketaux is the one licensed source. The free plan limits noted in code are 100 requests per day and 3 articles per request (`marketaux_service.py:9`).

Rate limits and fragility, from the code:
* The TradingView layer caps concurrency at 2 in flight TA calls with 0.5 s between starts (`screener_provider.py:247` to `:261`), retries twice with jitter (`:103` to `:127`), then imposes a 15 s shared cooldown (`:137` to `:176`). Comments describe recurring `30-90s empty-body` outages at the scanner (`:318`).
* Screeners scan the bundled symbol list in batches of 200 (`screener_service.py:240`). By symbol count that is 6 batches for KUCOIN, 15 for NYSE, 25 for NASDAQ, all under a 30 s wall clock budget (`screener_service.py:82` to `:88`), so large markets return partial data by design.
* `tradingview-screener` is pinned to exactly 3.0.0 because 3.2.0 "silently returns 0 rows for crypto/futures scans" (`pyproject.toml:40` to `:45`). The Docker build ignores `uv.lock` (`Dockerfile:14`, acknowledged at `pyproject.toml:43` to `:44`), so every other dependency floats on rebuild.
* Removed sources: Reddit and RSS scraping were deleted (deleted files `news_service.py`, `sentiment_service.py` in `git log --diff-filter=D`), but the README still advertises "Reddit + RSS news" (`README.md:123`, `:451` to `:453`).

## 3. MCP surface

Server level: FastMCP named `TradingView Multi-Market Screener` with a short `instructions` string that lists tool names (`server.py:114` to `:126`). One resource, `exchanges://list`, returns bundled list names (`server.py:1180` to `:1198`). One unauthenticated `GET /health` route (`server.py:1203` to `:1213`).

All 39 tools, inputs, returns (defaults and clamps from `server.py`):

| Tool | Inputs | Returns | Line |
|---|---|---|---|
| `top_gainers` | exchange, timeframe, limit (1 to 50) | rows of symbol, changePercent of the current bar, Bollinger fields | `:131` |
| `top_losers` | same | same, ascending | `:160` |
| `bollinger_scan` | exchange, timeframe, bbw_threshold, limit (1 to 100) | rows under the BBW threshold | `:179` |
| `rating_filter` | exchange, timeframe, rating (clamped to plus or minus 3), limit | rows with that Bollinger rating | `:208` |
| `coin_analysis` | symbol, exchange, timeframe | price, 54 TradingView indicator fields, derived scores, stock trade setup | `:234` |
| `consecutive_candles_scan` | exchange, timeframe, pattern_type, candle_count (2 to 5), min_growth, limit | pattern rows | `:264` |
| `advanced_candle_pattern` | exchange, base_timeframe, pattern_length (2 to 4), min_size_increase, limit | pattern rows, `fallback_reason` | `:294` |
| `volume_breakout_scanner` | exchange, timeframe, volume_multiplier, price_change_min, limit | rows | `:354` |
| `volume_confirmation_analysis` | symbol, exchange, timeframe | volume read for one symbol | `:393` |
| `smart_volume_scanner` | exchange, min_volume_ratio, min_price_change, rsi_range, limit (1 to 30) | rows | `:410` |
| `multi_agent_analysis` | symbol, exchange, timeframe | three rule sets and a BUY, SELL or HOLD label | `:442` |
| `egx_market_overview` | timeframe, limit | EGX summary | `:465` |
| `egx_sector_scan` | sector, timeframe, limit | sector rows | `:481` |
| `egx_sector_scanner` | timeframe, top_n_sectors, top_n_stocks, min_stock_score | rotation picks | `:499` |
| `egx_index_analysis` | index, timeframe, limit | constituents | `:524` |
| `egx_stock_screener` | timeframe, min_score, index_filter, limit | ranked stocks | `:541` |
| `egx_trade_plan` | symbol, timeframe | entries, stop, targets | `:565` |
| `egx_fibonacci_retracement` | symbol, lookback, timeframe | Fibonacci levels | `:580` |
| `smart_money_analysis` | symbol, exchange, period, interval | three local oscillators and a vote | `:599` |
| `egx_smart_money_scanner` | index, limit, period, min_score | ranked rows plus `skipped` | `:636` |
| `multi_timeframe_analysis` | symbol, exchange | trend per timeframe, weekly to 15m | `:665` |
| `market_sentiment` | symbol, category, limit | score, label, up to 5 article titles and URLs | `:693` |
| `financial_news` | symbol, category, limit | article title, URL, date, 300 char summary, source | `:705` |
| `combined_analysis` | symbol, exchange, timeframe | TA plus sentiment plus 3 news items plus a "recommendation" string | `:719` |
| `backtest_strategy` | symbol, strategy, period, capital, commission, slippage, interval, two include flags | metrics, last 5 trades, optional trade log and equity curve | `:791` |
| `compare_strategies` | symbol, period, capital, interval | leaderboard of 9 strategies | `:825` |
| `walk_forward_backtest_strategy` | symbol, strategy, period, capital, costs, n_splits, train_ratio, interval | folds and a verdict | `:845` |
| `yahoo_price` | symbol | quote with `quote_stale` flag | `:881` |
| `market_snapshot` | none | 14 fixed symbols | `:891` |
| `bitcoin_market_pulse` | none | BTC price, dominance, canned risk paragraph | `:899` |
| `stock_extended_hours` | symbol | pre, regular, post session prints | `:919` |
| `stock_options_chain` | symbol, expiry | calls and puts for one expiry | `:945` |
| `stock_options_unusual_activity` | symbol, top_n, min_volume, expiries | strikes by volume over open interest | `:974` |
| `futures_market_overview` | category, exchanges, limit, volume_min | contract rows | `:1019` |
| `futures_top_movers` | direction, exchanges, limit, volume_min | contract rows | `:1050` |
| `futures_category_snapshot` | category | front month quotes | `:1082` |
| `futures_watchlist` | none | static symbol list | `:1096` |
| `stock_screener` | country, stock_type, limit (up to 2000), exclude_otc, compact, sort_by | up to 2000 rows with description | `:1106` |
| `stock_prices` | tickers (up to 2000, `EXCHANGE:SYMBOL`) | rows plus `not_found` | `:1155` |

Every tool carries `readOnlyHint=True`, `destructiveHint=False`, `openWorldHint=True`, enforced by `tests/unit/test_tool_annotations.py:24` to `:38`. That matches the code: no tool writes, trades, or mutates anything.

Transport and binding:
* `stdio` by default; `streamable-http` on request (`server.py:1257` to `:1280`). Default host is `127.0.0.1` unless the `HOST` env var is set (`server.py:1264`).
* The Dockerfile runs `streamable-http --host 0.0.0.0 --port 8000` (`Dockerfile:42`), and `docker-compose.yml` publishes `"8080:8000"` on all host interfaces with `HOST=0.0.0.0` (`docker-compose.yml:8` to `:9`, `:14`).
* No authentication, API key, or per client limit exists anywhere in `src`.

## 4. MCP security

Severity is for traderbot's use case: an LLM agent that reads tool output and may act on markets.

### S1. Third party news text reaches the model unmarked (High in a trading agent)
* `financial_news` returns Marketaux `title` untruncated and unfiltered, `url` as given, and `summary` cut to 300 characters (`marketaux_service.py:215` to `:221`). `market_sentiment` returns titles cut to 120 characters (`:280`). `combined_analysis` embeds 3 news items and the sentiment block in its result (`server.py:769` to `:786`).
* Anyone who can get an article indexed by Marketaux controls that text. Nothing wraps it as data, strips instruction shaped content, or separates it from the tool's own fields.
* `_clean_text` strips tags first and decodes entities after (`marketaux_service.py:148` to `:153`), so an entity encoded tag such as `&lt;system&gt;` survives as a literal `<system>` in the output.
* Sentiment manipulation without any LLM: when Marketaux returns no entity scores (its text search path, which the code says covers most crypto, `marketaux_service.py:156` to `:158`), the score is a bag of words count over title and description (`:171` to `:183`, `:275` to `:277`). A headline stuffed with "rally surge breakout" moves the label, which then feeds the `confluence.recommendation` string in `combined_analysis` (`server.py:762` to `:785`).

### S2. Network transport with no auth, exposed by the shipped container config (High when deployed via Docker; not applicable on stdio)
* No auth plus `0.0.0.0` bind plus a compose port on all interfaces (section 3). Anyone who reaches the port can call every tool.
* What a caller can burn: the Marketaux daily budget is one process wide counter (`marketaux_service.py:46` to `:59`, `:112` to `:114`), so one client can exhaust news for all; the operator's paid residential proxy bandwidth on Yahoo paths; the operator's IP reputation with TradingView and Yahoo.
* Amplification per call: `stock_screener` and `stock_prices` return up to 2000 rows (`stock_screener_service.py:49` to `:50`); `futures_market_overview` and `futures_top_movers` pass `limit` through unbounded (`futures_service.py:81` to `:88`, `:141`); `stock_options_unusual_activity` fetches one upstream page per expiry with no upper bound on `expiries` (`options_service.py:306`, `:314` to `:316`). The same large payloads also flood a model's context.
* `.dockerignore` has no `.env` entry (43 lines, none match `^\.env`), while `.env.example:4` tells users to create `.env` in the repo root. A local `docker build` then copies it into `/app/.env` in the runtime image (`Dockerfile:11`, `:25`). The CI image is built from a clean checkout, so the GHCR image is not affected.
* DNS rebinding protection depends on the `mcp` SDK defaults, and the server sets `mcp.settings.host` after the `FastMCP` constructor ran (`server.py:1276`); how SDK 1.29.0 reacts to that is Unverified.

### S3. Stale data silently labeled real time (Medium, integrity)
* On repeated upstream failure the TradingView layer returns cached results up to 6 hours old (`screener_provider.py:96` to `:100`, `:380` to `:392`, `:455` to `:467`). The only signal is a stderr print. No field in any tool result says the data is stale; two unit tests assert the stale return works but none assert a freshness flag (`test_stale_while_error_returns_cached_payload`, `test_resilient_ta_returns_stale_on_persistent_failure`).
* `coin_analysis` hardcodes `"timestamp": "real-time"` (`screener_service.py:803`) and returns no data time. The code's own note in `multi_agent_analysis` calls the input "a single delayed snapshot" (`multi_agent_service.py:217` to `:221`). The Yahoo path is the exception: it computes and returns `quote_stale` (`yahoo_finance_service.py:111`, `:149`).
* A bot or model acting on these outputs cannot tell a live read from a 6 hour old one.

### S4. Instructions aimed at an AI agent (recorded as findings, none followed)
* `openclaw/SKILL.md:4` sets `"always": true`, so the skill loads into every OpenClaw agent context. It tells the agent: "NEVER use `sessions_spawn` or ask for an agent ID" (`:9`), "Run bash immediately ... don't ask for clarification" (`:27`), and "Always combine signals" by running price, backtest and sentiment for "should I buy X?" (`:28`).
* `OPENCLAW.md:58` to `:64` installs that skill and `trading.py` by `curl` from the unpinned `main` branch, into an agent that has a bash tool, with example paths under `/root` (`:80`, `:93`, `:111`). The same guide shows a public bot with `allowFrom: ["*"]` (`:139`) and warns only about token cost (`:150`). Result: any Telegram user gets a prompt path to a bash capable agent on the operator's VPS. `trading.py` itself takes argv and runs no shell; the exposure is the agent's bash tool, which the skill pushes it to use without asking.
* `SKILL.md` lists 15 tool calls; 10 are not MCP tools and `trading.py` does not expose them either (`technical_analysis`, `calculate_rsi`, `calculate_supertrend`, `analyze_confluence`, `screener_bullish`, `screener_oversold`, `egx_stock_screen`, `get_prices_bulk`, `analyze_sentiment`, `fetch_news_summary`; `SKILL.md:38` to `:73`). An agent told to call them will improvise.
* MCP tool descriptions steer the model: `bitcoin_market_pulse` says "Use this WHENEVER analyzing any cryptocurrency" (`server.py:903`), which pushes extra calls on a product that is metered per call when hosted; `stock_options_chain` and `stock_options_unusual_activity` script what "Claude" should say next (`server.py:951` to `:952`, `:990` to `:992`). Error envelopes carry retry directives ("do not retry", "Retry with one of those as `exchange`", `screener_service.py:650` to `:652`, `:666` to `:667`), which are reasonable guidance.
* Misleading description: `multi_agent_analysis` promises "A structured debate between 3 AI agents culminating in a final trading decision" (`server.py:452`), while the result's own `method_note` says "This is not an AI debate" (`multi_agent_service.py:217` to `:221`). A model reading only the description will present rule thresholds as AI consensus.
* No hidden content: a scan of every tracked text file for zero width, bidi, tag block, and private use characters found 1 hit, a zero width joiner inside the technologist emoji in a table header at `README.md:133`.

### S5. Model controlled symbol concatenated into Yahoo URLs (Low)
* `symbol` goes into the URL path unencoded and unvalidated at `yahoo_finance_service.py:56`, `backtest_service.py:63`, `extended_hours_service.py:42`, and `options_service.py:185`, `:218`, `:316`. The host is a fixed literal before the first `/`, so this is not SSRF to other hosts. It does allow arbitrary path and query on `query1` and `query2.finance.yahoo.com`, sent through the operator's proxy and, for options, with the cached Yahoo crumb and cookies (`options_service.py:118` to `:123`).
* `stock_screener`'s `country` is lowercased and passed to `Query().set_markets()` with no allowlist (`stock_screener_service.py:123`, `:136`); whether the library puts it into the URL path unencoded is Unverified. Host stays `scanner.tradingview.com` either way.
* `exchange` is validated against an allowlist of 39 values (`validators.py:238` to `:251`), and the scanner market segment always comes from fixed maps (`validators.py:259` to `:278`). Symbols sent to TradingView travel in JSON bodies, not URLs.

### S6. Credentials and secrets (Low to Medium)
* Live tree: no hardcoded secret found (pattern scan for common key formats: 0 hits). Secrets come from env: `MARKETAUX_API_TOKEN`, `PROXY_*` (`marketaux_service.py:79`, `proxy_manager.py:55` to `:64`).
* History: commit `25a52af` (2026-03-29) added `.env.example` with a non placeholder `PROXY_PASSWORD` (18 characters, type: Webshare proxy password) and a matching username. Commit `8baaac3` replaced both with placeholders the same day. The value stays in git history; value not recorded here, liveness Unverified.
* `proxy_manager.py:10` and `.env.example:26` carry a real looking username prefix as the "e.g." value. It is an account identifier, not a secret.
* The proxy URL uses `http://` to port 80 by default (`proxy_manager.py:57` to `:58`, `:83`), so proxy Basic auth crosses the network in cleartext to the proxy.
* `proxy_manager.py:30` to `:33` auto loads a `.env` from four directories above the module file; for an installed package that resolves outside the project (for example `lib/python3.x/.env`).
* Exception strings are returned to the model in many places (`yahoo_finance_service.py:172`, `:233`; `backtest_service.py:632`; `server.py:1047`, `:1150`). Whether an `httpx` proxy error string could include the proxy URL with credentials is Unverified.

### S7. Filesystem, shell, writes
* No `subprocess`, `os.system`, `eval`, `exec`, `pickle`, or file writes in `src` (`git grep`). File reads are limited to the bundled symbol lists; every `load_symbols` caller passes an allowlisted exchange or a literal (`server.py:319`, `scanner_service.py:73`, `screener_service.py:131`, `:231`, `:880`, `egx_service.py:63`, `:777`).
* The Docker runtime runs as a non root user (`Dockerfile:28` to `:29`).

### S8. Supply chain and registry metadata
* Every documented client config launches `uvx --from tradingview-mcp-server tradingview-mcp` with no version pin (`.codex-mcp.json:4` to `:9`, `README.md:169` to `:177`), so each start can pull a new PyPI release. Whether the PyPI artifacts match this repo is Unverified.
* `Dockerfile:5` to `:6` pipes the `uv` installer from the network into `sh`, unpinned. The registry workflow downloads `mcp-publisher` from `releases/latest` with no checksum (`.github/workflows/mcp-registry-publish.yml:23`). Actions are pinned by tag, not SHA.
* `server.json` lists only a `remotes` entry pointing at the author's hosted endpoint and no `packages` entry (`server.json:11` to `:16`). Installing from the MCP registry therefore sends your queries (symbols, which reveal what you trade) to a third party service whose code diverges from this repo (section 1). `glama.json` only names a maintainer.
* Documentation drift: README says 37 tools (`README.md:6`, `:65`, `:392`) against 39 registered; it lists 8 tool names that do not exist (`get_technical_analysis`, `get_multiple_analysis`, `get_bollinger_band_analysis`, `get_stock_decision`, `screen_stocks`, `scan_by_signal`, `get_candlestick_patterns`, `get_multi_timeframe_analysis`; `README.md:461` to `:468`); several docstrings exist to correct models that called those phantom names (`server.py:183` to `:185`, `:238` to `:239`, `:669` to `:670`). README claims "API Keys: None required" (`README.md:125`) while news needs a Marketaux token, and gives TA throttle defaults of 4 and 0.8 s (`README.md:103`) against 2 and 0.5 s in code.
* `feedparser` is a declared dependency (`pyproject.toml:21`) with 0 references in `src`.

## 5. Trading usefulness

* **Freshness:** TradingView data is fetched without an account, so it is TradingView's anonymous feed; the code itself calls it "delayed" (`multi_agent_service.py:218`) while labels say real time (S3). The exact delay per exchange is Unverified. Marketaux news is cached 4 hours (`marketaux_service.py:35`) under a tool described as `Real-time financial news` (`server.py:707`).
* **Timeframes:** TradingView tools accept 5m, 15m, 1h, 4h, 1D, 1W, 1M (`validators.py:5`). Backtests accept 1d or 1h over 1mo to 2y (`backtest_service.py:32` to `:33`).
* **Coverage:** 34 exchange ids across crypto and 9 stock markets, plus 5 forex and CFD venues for single symbol TA (`validators.py:28` to `:85`). Screeners iterate bundled lists, not the live exchange universe; 10 of 29 lists were last changed 2025-08-15, including binance, kucoin, bybit, okx and nasdaq, so new listings are missed and delisted names retried. Tadawul has an exchange id but no list file.
* **Indicator source:** single symbol tools consume 54 TradingView computed fields (RSI, MACD, EMA and SMA sets, BB, ADX, Stoch, pivots, `Recommend.*`, `VWAP`; grep of `indicators.get` keys in `indicators.py`). These are TradingView defaults for the current bar, with a few one bar lags (`RSI[1]`, `Mom[1]`, `AO[1]`); parameters cannot be changed. Derived values (Bollinger rating, BBW, change, stock score, entries, stops, targets) are local heuristics in `indicators.py` (1697 lines). `changePercent` in screeners is the current bar's open to close move at the chosen timeframe, not a 24 hour change (`indicators.py:5` to `:6`, `screener_service.py:325`).
* **History:** no OHLCV history comes from TradingView. Backtests and smart money use Yahoo daily or hourly bars and compute indicators locally (`indicators_calc.py`, `smart_money_service.py:28`).
* **Backtester limits:** long only; all 9 strategies fill at the close of the signal bar (`"entry_price": price` with `price = candles[i]["close"]`, 9 occurrences in `backtest_service.py`), a mild look ahead at daily resolution; flat per side cost (`:349` to `:358`); Sharpe annualized with 252 days and 6.5 hours per day for 1h bars (`:40`), wrong for 24/7 crypto; the walk forward optimizes nothing, as its own caveat says (`:943` to `:946`), yet the lowest verdict text still reads "OVERFITTED ... do not trade live" (`:923`). Credit where due: open positions are force closed at the end rather than dropped (`:118` to `:132`), and daily bars are dividend adjusted (`:87` to `:101`).
* **What a bot could do with it:** screen crypto and some equity venues for Bollinger squeezes, volume spikes, and movers on the current bar; pull a TradingView indicator snapshot for one symbol; get Yahoo quotes and options chains; run toy backtests.
* **What it cannot do:** place, cancel, or track orders; read balances or positions; get tick, order book, or trade data; get historical TradingView bars; guarantee freshness; stream anything (every call is a poll).

## 6. Code quality and tests

* **Size:** 26 Python source files, 11,679 lines; the largest are `indicators.py` (1697), `server.py` (1284), `screener_service.py` (1262), `egx_service.py` (1189), `backtest_service.py` (960).
* **Tests:** 105 functions in 30 files (97 unit, 8 stress), 8 `parametrize` uses, 18 files using mocks or monkeypatch. They are regression tests for past bugs: typed error envelopes, exchange and alias handling, venue fallback, retry and stale cache behavior, backtest input validation and metric fixes, async offload, and tool annotations. CI runs only the unit suite (`test.yml:38`); stress tests need live upstream (`pyproject.toml:68` to `:75`). No test covers symbol or path input validation, output size limits, or untrusted text in tool output (`git grep` for inject, traversal, sanitize in `tests`: only exchange alias hits).
* **Error handling:** 105 `except Exception` clauses in `src`, 20 of them followed directly by `pass`, 0 bare `except:`. Upstream failures become typed envelopes with `code`, `retryable`, `retry_after_s` in most TradingView paths (`errors.py:57`, `:177` to `:211`). 11 of 39 handlers have no `try` in the server layer and rely on the service to catch (AST scan: `market_sentiment`, `financial_news`, `backtest_strategy`, `compare_strategies`, `walk_forward_backtest_strategy`, `yahoo_price`, `market_snapshot`, `bitcoin_market_pulse`, `stock_extended_hours`, `futures_category_snapshot`, `futures_watchlist`). Error shapes are mixed: typed envelopes, `{"error": "..."}` strings (`backtest_service.py:618` to `:632`), and `{"symbol", "error"}` dicts (`yahoo_finance_service.py:172`); the README admits partial adoption (`README.md:267`).
* **Logging:** 16 `print` calls to stderr, no `logging` module use.
* **HTTP stacks:** three (urllib, `requests`, `httpx`) plus two scanner libraries.
* **Comment style:** long dated rationale comments throughout (`added 2026-05-20`, `hardened 2026-05-20`, `Field-tested`), consistent with the heavy AI assisted workflow seen in the history.

## 7. Maintenance

Commits on `main` by month:

| Month | Commits |
|---|---|
| 2025-08 | 11 |
| 2025-10 | 4 |
| 2025-11 | 2 |
| 2025-12 to 2026-02 | 0 |
| 2026-03 | 25 |
| 2026-04 | 22 |
| 2026-05 | 21 |
| 2026-06 | 15 |
| 2026-07 | 25 |
| 2026-08 | 20 |
| 2026-09 | 5 |

* 139 of 150 commits landed in the last 12 months; last commit 2026-09-01.
* 21 distinct author names; the owner has 98 commits (as "Ahmet Taner Atila" under two emails and "taner.atila"), 52 come from others, led by Galal Shaban (20: EGX support, backtest metric fixes, input validation, and the rewording of `multi_agent_analysis` as a rule based summary). 62 commits were committed by "GitHub", i.e. web merges of PRs.
* AI share: 22 of 150 commits (14.7%) carry an explicit `Co-authored-by` trailer naming a Claude model (Opus 4.6 to 5, Sonnet 4.6, Fable 5), 10 of them in 2026-08. This is a lower bound: commits without a trailer can still be AI written, and the code comments read that way.
* Security policy exists (`SECURITY.md`), supports only 0.9.x, and lists upstreams as TradingView, Yahoo and Marketaux, omitting CoinGecko and the symbol search endpoint (`SECURITY.md:27` to `:28`).

## 8. Value for traderbot

**Call: rebuild the few good parts in our own code; do not use as is, do not fork, do not point traderbot at the hosted remote.**

Why not use as is:
* The data layer is the problem, not the wrappers. The unauthenticated TradingView scanner gives current bar snapshots of fixed parameter indicators, of unverified delay, with outages the code itself documents, and with terms of service exposure. A bot needs exchange native data (official exchange REST and websocket APIs, or a licensed vendor) with explicit timestamps.
* S3 alone disqualifies it for automated decisions: a 6 hour old snapshot comes back labeled `"real-time"`.
* S1 makes it a prompt injection carrier if an LLM in the loop can trade.
* The hosted remote sends our watchlist to a third party.

Why not fork: a large share of the 11,679 lines is EGX specific (`egx_service.py`, `egx_indices.py`, `egx_sectors.py`: 1,990 lines) or heuristic scoring and "trade setups" (`indicators.py`: 1,697 lines); the valuable parts are small, and a fork would inherit the stale data behavior, three HTTP stacks, and the bundled symbol lists that go stale.

Worth rebuilding (as ideas, in original code):
* The resilience layer shape: retry with jitter, shared failure cooldown, bounded in flight semaphore, bounded stale cache (`screener_provider.py:89` to `:394`), but with an explicit `as_of` and `stale: true` in every result.
* Typed error envelopes with `retryable` and `retry_after_s` so an agent can branch without parsing prose (`errors.py`).
* Venue fallback that says what it did (`requested_exchange`, `resolved_exchange`, `resolution_note`, `screener_service.py:730` to `:741`).
* The annotation contract test (`tests/unit/test_tool_annotations.py`) and the import time async offload wrapper (`server.py:1233` to `:1252`).
* Backtest correctness fixes as a checklist for our own engine: force close open positions, dividend adjust, validate cost inputs, report a buy and hold benchmark with the same costs.
* Treat any news or third party text tool as tainted input: wrap it, cap it, and keep it out of any path that can reach an order. Same idea as twin's taint model and approval gate.

License terms: MIT (`LICENSE`). Copying code is allowed if the copyright and permission notice travel with copies or substantial portions. Rebuilding the patterns in our own code needs no notice; a provenance line in our own records is enough.

## Unverified

* TradingView's and Yahoo's current terms of service text, and whether they forbid this use; no network access.
* The real data delay of TradingView's anonymous scanner feed per exchange, and of Yahoo quotes.
* How `mcp` 1.29.0 FastMCP handles DNS rebinding protection and allowed hosts when `settings.host` is changed after construction (`server.py:1276`).
* Whether `tradingview-screener` 3.0.0 `set_markets()` places `country` into the URL path unencoded.
* Whether `httpx` or urllib proxy error messages returned to the model can contain the proxy URL with credentials.
* Whether the 18 character proxy password in commit `25a52af` is still valid.
* Whether the PyPI package `tradingview-mcp-server` and the GHCR image match this commit.
* What code runs at `mcp.cryptosieve.com`, and how its quotas and chart features are implemented.
* Whether the README claims of `Live / Real-Time` data (`README.md:124`) hold for any exchange.
