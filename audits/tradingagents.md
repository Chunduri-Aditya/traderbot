# Audit: `TauricResearch/TradingAgents`

Audited 2026-09-28 against commit `35543d0` (HEAD of `main`, "TradingAgents v0.5.1 release", 2026-09-24). Static review only: nothing in the repo was installed or executed. One network fetch: the arXiv abstract page for 2412.20138.

**Verdict**
1. A LangGraph pipeline of 12 LLM roles (4 analysts, bull and bear researchers, research manager, trader, 3 risk debaters, portfolio manager) that turns one ticker and one date into a 5 tier rating (Buy, Overweight, Hold, Underweight, Sell); it places no orders and simulates no fills.
2. Worth studying and rebuilding in parts (tool side date clamping, deferred settlement with point in time lessons, the REVIEW sentinel, the staged debate plus risk review); not worth adopting as an engine or as evidence that LLM debate earns alpha.
3. Biggest caveat: its historical results cannot validate the live system. With default vendors a backtest runs with no ticker news, no social data and no prediction markets, prices are back adjusted, yfinance fundamentals leak through the filing lag, nothing guards against the LLM having memorized the test window, costs are ignored, and no code in the repo reproduces the paper's return, Sharpe or drawdown figures.

## Facts

| Item | Value | Source |
|---|---|---|
| Kind | Python library plus Typer CLI; LangGraph `StateGraph` | `pyproject.toml:10`, `:36`; `tradingagents/graph/setup.py:95` |
| Version | 0.5.1 | `pyproject.toml:7` |
| License | Apache 2.0; no NOTICE file tracked | `LICENSE:1` to `:2`; `git ls-files \| grep -ic notice` = 0 |
| Paper | arXiv 2412.20138, Xiao, Sun, Luo, Wang; v1 2024-12-28, v7 2025-06-03 | `README.md:382` to `:390`; arXiv abstract page |
| Commits (HEAD) | 393, 2024-12-28 to 2026-09-24 | `git rev-list --count HEAD` |
| First code release | v0.1.0 on 2025-06-05 (`cc97cb6`); earlier commits held only a project page | `git log --reverse` |
| Authors | 24 distinct names; 335 of 393 commits (85.2%) under `Yijia-Xiao` or `Yijia Xiao`; since 2026-03-01, 282 of 302 (93.4%) | `git shortlog -sn`; `git log --since` |
| Tracked files | 193 total, 166 `.py` | `git ls-files` |
| Python lines | `tradingagents/` 9,250; `cli/` 2,401; `tests/` 11,232 | `wc -l` |
| Tests | 75 files, 760 test functions (394 top level, 366 in classes), 65 `parametrize` decorators, 1 `integration` marker | AST count script; `git grep` |
| LLM providers | 20 provider ids (16 OpenAI compatible plus Anthropic, Google, Azure, Bedrock) | `tradingagents/llm_clients/openai_client.py:212` to `:234`; `llm_clients/factory.py:36` to `:49` |
| Default models | `gpt-6-sol` (deep), `gpt-6-luna` (quick), provider `openai` | `tradingagents/default_config.py:81` to `:83` |
| Runtime deps | 16, all floor pins, 0 upper bounds, no lockfile; `requirements.txt` is `.` | `pyproject.toml:12` to `:29`; `ls uv.lock poetry.lock` |
| Order execution | None; no broker code | `git grep` for alpaca, ibkr, ccxt, place_order, submit_order: only `README.md:104` |
| Secrets in tree or history | None found | regex scan exit 1 on tree; `git log -G` 0 commits; positive control `OPENAI_API_KEY` hit 15 commits |
| Agent instruction files in repo | None (no CLAUDE.md, AGENTS.md, cursor or copilot rules) | `git ls-files` grep exit 1 |

## 1. What it is

The code implements the TradingAgents paper (arXiv 2412.20138). The abstract claims "notable improvements in cumulative returns, Sharpe ratio, and maximum drawdown" over baselines; the abstract page gives no numbers, tickers or dates. The README frames the tool as research only: "Trading performance may vary ... It is not intended as financial, investment, or trading advice" (`README.md:74`), and "Backtest results are not guaranteed to match any published figure ... not as a strategy with a fixed, replicable return" (`README.md:371`).

Maturity: active, single maintainer, fast moving. v0.5.1 moved import paths (`README.md:35`). There is a CI gate (pytest on Python 3.10 to 3.13 plus a non UTC time zone run, ruff, clean install smoke; `.github/workflows/ci.yml:19` to `:23`, `:36`, `:52`, `:67`). The engineering since 2026-03 is careful about data integrity; the research claim that motivated the repo is not reproducible from it (section 3).

## 2. Architecture

### Graph

```
START
  -> Market Analyst  <-> tools_market   (get_stock_data, get_indicators, get_verified_market_snapshot)
  -> Msg Clear Market
  -> Sentiment Analyst                  (no tools; news + StockTwits + Reddit prefetched into prompt)
  -> Msg Clear Sentiment
  -> News Analyst    <-> tools_news     (get_news, get_global_news, get_macro_indicators, get_prediction_markets)
  -> Msg Clear News
  -> Fundamentals Analyst <-> tools_fundamentals (get_fundamentals, balance sheet, cashflow, income, insider)
  -> Msg Clear Fundamentals
  -> Bull Researcher -> Bear Researcher -> ... (2 x max_debate_rounds turns, Bull always opens)
  -> Research Manager [deep]   (sees debate history only)
  -> Trader                    (sees research plan, market report, portfolio)
  -> Aggressive -> Conservative -> Neutral -> ... (3 x max_risk_discuss_rounds turns)
  -> Portfolio Manager [deep]  (sees research plan, trader plan, risk history, past lessons)
  -> END   => final_trade_decision, parsed to Buy|Overweight|Hold|Underweight|Sell|REVIEW
```

* Analysts run strictly in sequence; the last analyst's clear node hands to "Bull Researcher" (`graph/setup.py:112` to `:125`). Default selection is all four in the order market, social, news, fundamentals (`graph/setup.py:65`, `graph/trading_graph.py:48`). Each tool analyst loops agent to tool node until it answers without tool calls (`graph/setup.py:43` to `:47`, `:115` to `:119`).
* Research debate: stop when `count >= 2 * max_debate_rounds` (`graph/conditional_logic.py:15` to `:18`); the next speaker is chosen by the prefix of `current_response` (`:19` to `:21`). Default `max_debate_rounds = 1` (`default_config.py:115`), so one bull turn and one bear turn. The inline comment `3 rounds of back-and-forth` (`conditional_logic.py:17`) is stale.
* Risk debate: stop when `count >= 3 * max_risk_discuss_rounds`; order Aggressive, Conservative, Neutral (`conditional_logic.py:25` to `:33`). Default 1 round, so three turns (`default_config.py:116`).
* Model split: every analyst, both researchers, the trader, the three risk debaters and the reflector use the quick model; only the Research Manager and Portfolio Manager use the deep model (`graph/setup.py:79` to `:93`; `graph/trading_graph.py:106`).
* Speaking order is fixed: Bull opens and Bear closes the research debate, Aggressive opens the risk debate (`graph/setup.py:124`, `:135`). The judge prompts ask for order independence in words only (`agents/managers/research_manager.py:36`, `agents/managers/portfolio_manager.py:69`).

### State passing

* One `AgentState` dict. Initial state carries ticker, date, asset type, resolved instrument context, past lessons, portfolio text, two debate sub states with string histories and counters, and four empty report slots (`graph/propagation.py:30` to `:66`).
* Analyst message history is wiped between analysts and replaced by a placeholder human message (`agents/context.py:211` to `:235`); only the report string survives in state.
* Debates append whole turns to strings (`agents/researchers/bull_researcher.py:53` to `:61`, `agents/risk_mgmt/aggressive_debator.py:50` to `:64`).
* Who reads what: bull, bear and all three risk debaters receive all four full analyst reports plus the growing transcript (`bull_researcher.py:18` to `:47`; `aggressive_debator.py:23` to `:44`; `git grep -c "report_or_absent(state"` = 4 in each of the 5 debater files). The Research Manager reads only the debate transcript, not the reports (`research_manager.py:18` to `:51`). The Trader reads the research plan, the market report and the portfolio (`agents/trader/trader.py:28` to `:85`). The Portfolio Manager reads research plan, trader plan, past lessons and the risk transcript, not the reports (`portfolio_manager.py:30` to `:65`).
* Structured output (Pydantic) for Sentiment Analyst, Research Manager, Trader and Portfolio Manager, rendered back to markdown; any exception falls back to one free text call (`agents/structured.py:59` to `:89`). The final signal is regex parsed from that markdown; unparseable output becomes `REVIEW`, never Hold (`agents/rating.py:27` to `:31`, `:49` to `:88`; `graph/trading_graph.py:344`, `:388` to `:390`).

### Data sources

| Source | Used for | Key | Access | Point in time handling |
|---|---|---|---|---|
| Yahoo via `yfinance` (default for prices, indicators, fundamentals, news) | OHLCV, stockstats indicators, statements, profile, news, insider rows | none | unofficial client of Yahoo endpoints | prices filtered to `<= curr_date` (`dataflows/vendors/yahoo/ohlcv.py:261`); profile withheld for past dates (`dataflows/date_window.py:98` to `:124`); statements cut by period end, not filing date (`yahoo/fundamentals.py:92` to `:100`, `:202` to `:213`); news filtered by publish time but the feed serves only the latest items (`yahoo/news.py:85`, `:97`) |
| SEC EDGAR | statements as filed | none (`User-Agent` header; default `contact@example.com`) | public API | fact kept only if `filed <= curr_date`, first reported value (`dataflows/vendors/sec_edgar.py:147` to `:185`, `:96`) |
| Alpha Vantage | prices, indicators, fundamentals, news | `ALPHA_VANTAGE_API_KEY` | API | news queried with `time_from`/`time_to` (`vendors/alpha_vantage/news.py:28` to `:29`) |
| FRED | macro series | `FRED_API_KEY` | API | vintage pinned with `realtime_start = realtime_end` (`vendors/fred.py:184` to `:193`) |
| Polymarket Gamma | event odds | none | public API | withheld for any date before today (`vendors/polymarket.py:86` to `:91`) |
| Reddit | posts in r/wallstreetbets, r/stocks, r/investing | none | scrapes the public Atom search feed with a custom `User-Agent` (`vendors/reddit.py:68`, `:91` to `:99`) | `t=week` only, then window filter (`reddit.py:96`, `:38` to `:49`) |
| StockTwits | cashtag stream | none | public JSON endpoint (`vendors/stocktwits.py:30`) | latest messages only, then window filter |
| TypeSafe Jev (optional) | classify social posts | `TYPESAFE_API_KEY` | third party API `api.typesafe.ai` (`agents/post_screen.py:26`) | n/a |

Vendor chains are explicit; no silent fallback to unconfigured vendors (`dataflows/router.py:195` to `:209`). A vendor with no data returns a `NO_DATA_AVAILABLE` sentinel telling the model not to fabricate (`router.py:247` to `:267`).

### Memory and reflection

* Every finished run appends `[date | ticker | rating | pending]` plus the full decision to a markdown log, default `~/.tradingagents/memory/trading_memory.md` (`decision_log.py:30` to `:52`; `default_config.py:75`).
* At the start of the next run for the same ticker, pending entries whose holding window has traded are settled: raw return and alpha over `holding_period_days = 5` trading days against a regional benchmark (SPY for US), then one quick model call writes a 2 to 4 sentence reflection (`graph/settlement.py:38` to `:132`; `graph/reflection.py:20` to `:61`; `default_config.py:158`, `:171`).
* Up to 5 same ticker entries (full decision plus reflection) and 3 cross ticker reflections are injected into the Portfolio Manager prompt only (`decision_log.py:73` to `:110`; `portfolio_manager.py:38` to `:43`).
* For past dated runs, only lessons whose `resolved:` date is on or before the trade date are injected (`graph/trading_graph.py:130` to `:139`; `decision_log.py:86` to `:87`).
* Optional LangGraph SQLite checkpointing per ticker for crash resume (`graph/checkpointer.py:41` to `:51`).

## 3. Trading and eval validity

**No orders.** The system emits a rating and prose. `backtest.py:9` to `:14` says so directly: "It is not a portfolio simulator ... Turning a rating into a filled order needs a quantity, a fill price and a cash ledger, none of which the system has." `README.md:104` still says "If approved, the order will be sent to the simulated exchange and executed"; no simulated exchange exists (broker and exchange grep above).

**How returns are produced.** `run_backtest` loops tickers then dates, calls `propagate` per cell into a private decision log, then settles (`backtest.py:128` to `:175`). Scoring is per rating: hit rate is the share of cells whose alpha sign matches the rating's direction, Hold gets no hit rate, plus mean alpha (`backtest.py:89`, `:192` to `:200`). Alpha is simple excess return, `raw - bench_ret`, not beta adjusted (`graph/settlement.py:69` to `:71`), stored rounded to 0.1 percentage point (`backtest.py:64` to `:74`). No Sharpe, drawdown or cumulative return code exists anywhere (`git grep -niE "sharpe|drawdown|cumulative return"` exit 1).

**Look ahead, item by item (default config).**

| Path | Status | Evidence |
|---|---|---|
| Model supplied dates in tool calls | Guarded. Tools take `trade_date` from graph state via `InjectedState`; any later date the model asks for is clamped | `agents/tools.py:1` to `:5`, `:34`, `:59`; `dataflows/date_window.py:72` to `:95`; `tests/test_tool_date_enforcement.py:96`, `:103` |
| Price rows after the date | Guarded | `yahoo/ohlcv.py:261` |
| Price levels | Leak of a mild kind. `yf.download(..., auto_adjust=True)` back adjusts history with splits and dividends that happened after the trade date, so the levels the Trader grounds entries and stops in are not the prices traders saw | `yahoo/ohlcv.py:243` to `:250`; `trader.py:37` to `:61` |
| Price coverage | The indicator and snapshot path downloads only 5 years ending at the wall clock date, so runs dated earlier have no indicator data | `yahoo/ohlcv.py:215` to `:217`, `:261` |
| Same bar entry | The analysis sees the trade date's full bar (`<= curr_date`), and settlement enters at that same close (`stock.iloc[0]` from `history(start=trade_date)`). Not executable; mildly optimistic | `yahoo/ohlcv.py:261`; `graph/settlement.py:60`, `:69`; `yahoo/market.py:273` to `:280` |
| Ticker news (Yahoo) | No leak, but starved. `get_news(count=20)` returns the latest items; the window filter then drops everything for past dates, and tests assert the "unavailable" marker | `yahoo/news.py:85`, `:97`; `tests/test_news_lookahead.py:121`, `:165` |
| Social (Reddit, StockTwits) | No leak, but starved: Reddit searches only the last week, StockTwits serves the latest messages. The module says `a historical run's sentiment inputs are not point-in-time` | `reddit.py:96`; `agents/analysts/sentiment_analyst.py:10` to `:13`, `:61` to `:68` |
| Polymarket | Withheld for past dates | `polymarket.py:86` to `:91` |
| Fundamentals (yfinance default) | Leak. Statements are cut by fiscal period end, not filing date, so a run dated between quarter end and the 10-Q sees figures that were not public; the vendor also serves current (restated) values. The code admits the first half in a header string | `default_config.py:141`; `yahoo/fundamentals.py:92` to `:100`, `:202` to `:213` |
| Company profile | Withheld for past dates, but the instrument identity (name, sector, industry from today's profile) is still injected into every prompt with a caveat | `date_window.py:98` to `:124`; `agents/context.py:57` to `:96`, `:146` to `:152` |
| Insider rows | Dated by transaction, not Form 4 filing; disclosed in output | `yahoo/fundamentals.py:151` to `:185` |
| Macro (FRED) | Guarded by vintage pinning | `fred.py:184` to `:193` |
| Past lessons in memory | Guarded by `resolved:` date filter | `decision_log.py:86` to `:87` |
| LLM training data covering the test window | Not addressed. No prompt tells the model to ignore knowledge after the analysis date; prompts only say "Today's date is {current_date}; treat it as 'now'" | `agents/analysts/market_analyst.py:64`; `git grep -niE "knowledge\|hindsight\|events after" -- tradingagents` exit 1 |

The combined effect is the core finding. A historical cell with default vendors gives the agents back adjusted prices and indicators, period end cut and restated financials, and placeholders where news, social and prediction market inputs would be, while the models themselves may already know how the stock moved. The system under backtest is a different system from the live one (which gets live news and social text), and it is contaminated in a direction that flatters the result. Treat any historical score from this repo as unvalidated until run only on dates after the model's training cutoff, which is effectively forward paper trading.

**Transaction costs.** None: no commission, spread or slippage term anywhere (`git grep -niE "slippage|commission|transaction cost|bid.?ask"` returned only the FRED series name `10y_2y_spread`, `vendors/fred.py:52`).

**Universe and period.** No default universe or period ships. The README example is 2 tickers weekly over two months, 9 dates, 18 cells (`README.md:338` to `:346`; count computed with the same grid rule as `backtest.py:33` to `:50`). The grid default `every_n_days = 1` (`backtest.py:33`) with a 5 trading day holding window produces overlapping outcomes, and the summary reports no confidence interval or test (`backtest.py:107` to `:125`).

**Reproducibility.** Not reproducible, and the repo says so: "One model sampling per cell, and text feeds are not archived, so these figures are indicative rather than repeatable" (`backtest.py:121` to `:124`; `README.md:351` to `:371`). Other blockers: no lockfile and no upper pins (`pyproject.toml:12` to `:29`); no LLM response cache; default reasoning models that largely ignore temperature (`README.md:359`). The paper's numbers cannot be tied to any commit: the first code release (`cc97cb6`, 2025-06-05) postdates paper v1 by five months, and its config pointed at a developer's local dataset, `"data_dir": "/Users/yluo/Documents/Code/ScAI/FR1-data"` (`git show cc97cb6:tradingagents/default_config.py`, line 5), which was never shipped. 15 commits addressing look ahead or point in time landed between 2026-03-29 and 2026-09-18 (`git log -i -E --grep='look.?ahead|point.in.time'`), all after paper v7 (2025-06-03). Whatever produced the paper's figures predates every one of those guards.

## 4. Cost and latency

LLM calls per ticker per date, counted from the graph code with defaults (4 analysts, 1 debate round, 1 risk round):

| Stage | Calls | Model | Source |
|---|---|---|---|
| Market, News, Fundamentals analysts | at least 2 each (one tool round plus the final report); one more per extra tool round | quick | `graph/setup.py:115` to `:119` |
| Sentiment analyst | 1, plus 1 if structured output fails | quick | `sentiment_analyst.py:105` to `:111`; `structured.py:73` to `:88` |
| Bull, Bear | 2 x `max_debate_rounds` = 2 | quick | `conditional_logic.py:15` to `:18` |
| Research Manager | 1 (+1 fallback) | deep | `research_manager.py:53` to `:59` |
| Trader | 1 (+1 fallback) | quick | `trader.py:87` to `:93` |
| Aggressive, Conservative, Neutral | 3 x `max_risk_discuss_rounds` = 3 | quick | `conditional_logic.py:25` to `:28` |
| Portfolio Manager | 1 (+1 fallback) | deep | `portfolio_manager.py:81` to `:87` |
| Reflection on each settled prior decision | 1 per entry, on the next run for that ticker | quick | `graph/settlement.py:100` to `:114` |

Minimum per run: 15 LLM calls (13 quick, 2 deep), plus reflections, plus up to 4 structured output fallbacks. The upper bound is set only by `recursion_limit = 100` graph steps (`default_config.py:117`; `graph/propagation.py:75`), since analyst tool loops have no own cap. The market analyst prompt asks for `get_stock_data` first, then up to 8 indicators, then the snapshot (`market_analyst.py:45` to `:47`), which invites several tool rounds; the real count depends on how the model batches calls (unmeasured). General formula: sum over tool analysts of (tool rounds + 1) + 1 + 2R_d + 1 + 1 + 3R_r + 1.

Cost drivers:
* Context, not call count. Each of the 5 debater calls carries all four full reports (prompted as "very detailed" with a Markdown table, `market_analyst.py:49` to `:50`) plus the whole transcript so far, so tokens grow roughly quadratically in rounds.
* Default reasoning models (`default_config.py:82` to `:83`) add hidden reasoning tokens on every call.
* The PM prompt carries up to 5 full past decisions plus 3 reflections (`decision_log.py:74`, `:319` to `:327`).
* A backtest multiplies all of this by cells; the README example is at least 270 LLM calls (18 cells x 15) plus 18 reflections.

Latency: fully serial (analysts chained, debates turn by turn), so wall time is the sum of every call. Adders: a Reddit 429 waits up to 60 s once (`reddit.py:128`, `:195` to `:205`); yfinance rate limits retry at 2, 4, 8 s (`yahoo/ohlcv.py:41` to `:57`); with Jev enabled, one HTTP request per post over 16 threads (`post_screen.py:33`, `:160` to `:174`).

## 5. Security

**Top finding: untrusted social and news text reaches the system prompt verbatim, and the decision it shapes persists into later runs.**
* The Sentiment Analyst puts Yahoo news, StockTwits bodies (up to 280 chars each) and Reddit titles plus 240 char excerpts inside the system message between plain tags `<start_of_reddit>` ... `<end_of_reddit>` (`sentiment_analyst.py:79` to `:90`, `:131` to `:154`; `stocktwits.py:139`, `:151`; `reddit.py:311` to `:317`). Nothing escapes the closing tags or marks the content as data; `git grep -niE "injection|untrusted|sanitiz"` over product code finds only CLI preference sanitizing. Anyone who can post on StockTwits or Reddit with the cashtag can write into a system prompt.
* The News Analyst sees Yahoo headlines and summaries as tool results and can then choose further tool calls (`news_analyst.py:12` to `:17`, `:55` to `:56`).
* Action surface: every tool is a read only data fetch. The model controls `symbol`, `topic`, indicator names and look back values; dates are clamped (`agents/tools.py:17` to `:284`). No write, shell, file or order tool exists. So the reachable harm is manipulation of the rating itself; in a bot that maps ratings to orders, that is the attack.
* Persistence: the PM's final decision text is stored verbatim and injected into future PM prompts, including other tickers' runs as "cross ticker lessons" (`decision_log.py:49` to `:52`, `:103` to `:109`; `portfolio_manager.py:38` to `:43`). The log is parsed by splitting on an HTML comment the code assumes "cannot appear in LLM prose output" (`decision_log.py:12` to `:13`, `:61`). A decision that reproduces the separator and a forged tag line with `resolved:` creates a fake settled lesson that passes the point in time filter (`decision_log.py:86` to `:87`, `:296` to `:301`). The idempotency guard scans every line of the log, decision bodies included (`decision_log.py:43` to `:47`), so a forged tag line can also block a later real entry. Exploitability depends on the model echoing attacker text; the mechanism is in the code.

**Credential routing through a discovered `.env`.** The package calls `load_dotenv(find_dotenv(usecwd=True))` at import, which walks up from the working directory (`tradingagents/__init__.py:8` to `:9`). `TRADINGAGENTS_LLM_BACKEND_URL` becomes `backend_url` (`default_config.py:14`), which outranks the provider default, and the provider key from the environment is attached to that client (`openai_client.py:291` to `:307`). Running the CLI inside an untrusted checkout that carries a planted `.env` sends the user's key to the planted host. Standard dotenv behavior, still worth a guard in traderbot.

**Docker image can bake Azure credentials.** `.dockerignore:3` excludes `.env` but not `.env.enterprise`, which the README tells Azure users to create in the repo root (`README.md:178`). The builder runs `COPY . .` and the final stage copies `/build` into the image (`Dockerfile:10`, `:26`).

**Phone home.** The CLI fetches `https://api.tauric.ai/v1/announcements` on start (`cli/config.py:3`; `cli/selections.py:71` to `:72`), renders the returned string as Rich markup in a panel, and can block for Enter when the server says so (`cli/announcements.py:16` to `:50`). Low severity: it leaks IP and usage timing and gives a remote party styled text and hyperlinks in the terminal. Exceptions are swallowed silently (`cli/announcements.py:24`).

**Data egress.** With `TYPESAFE_API_KEY` set, every fetched social post plus the instrument name goes to `api.typesafe.ai` (`post_screen.py:74` to `:86`, `:160` to `:169`). Portfolio holdings (cash, positions, average price) go to the LLM provider in the Trader, risk and PM prompts (`trader.py:35`, `:72`; `aggressive_debator.py:28`, `:39`; `portfolio_manager.py:31`, `:49`).

**Deserialization and execution.** No `pickle`, `yaml.load`, `eval`, `exec`, `subprocess` or `os.system` in product code; the only hits are in tests (`git grep` over `*.py`). Reddit Atom XML is parsed with stdlib `ElementTree` (`reddit.py:28`, `:193`) after a 5 MB read cap (`reddit.py:155` to `:167`). Checkpoints use LangGraph's `SqliteSaver` over a local file (`graph/checkpointer.py:41` to `:51`); `langgraph-checkpoint-sqlite>=2.0.0` has no upper or safe floor (`pyproject.toml:18`), see Unverified.

**Key hygiene, positives.** Keys sent as query parameters are scrubbed from exception text before they can reach logs or prompts (`dataflows/net.py:6` to `:24`; `vendors/fred.py:137` to `:143`; `vendors/alpha_vantage/common.py:95` to `:96`). The CLI writes a pasted key to `.env` created or tightened to mode 0600 (`cli/prompts.py:631` to `:637`). Ticker and run id values are validated before use in paths (`yahoo/ohlcv.py:209`; `graph/checkpointer.py:22`; `graph/trading_graph.py:379`; `backtest.py:145`). Future trade dates are rejected (`graph/trading_graph.py:29` to `:40`). No live secrets in tree or history.

**Repo text addressed to agents.** None found. Nothing read during this audit instructed an agent to act.

## 6. Code quality and tests

* 760 test functions in 75 files. The suite blocks sockets for every test not marked `integration` (`tests/conftest.py:35` to `:45`); exactly one test carries that marker. The end to end test drives the whole graph with a scripted chat model that calls every bound tool once (`tests/test_graph_end_to_end.py:1` to `:6`, `:47` to `:75`).
* What the tests assert: wiring (graph edges, path maps), date clamping and window boundaries, point in time memory, rating parsing, vendor routing and error sentinels, config and env precedence, symbol normalization, prompt text shape (`tests/test_prompt_integrity.py:20`, `:38`, `:55`), backtest bookkeeping and scoring sign logic (`tests/test_backtest.py:20` to `:271`). Largest files: `test_memory_log.py` (77 tests), `test_structured_agents.py` (35), `test_reddit_fallback.py` (27).
* What they do not assert: anything about decision quality. There is no eval harness against a real model, no fixed dataset, no baseline comparison. The tests prove the plumbing, not the claim.
* Error handling is deliberate: typed vendor errors mapped to sentinels (`router.py:211` to `:291`), optional categories degrade instead of aborting (`router.py:97` to `:102`). 23 `except Exception` sites in product code; most log or re raise as typed errors. The structured output fallback hides schema failures behind a warning and a free text retry (`structured.py:82` to `:88`).
* Comments cite 84 distinct issue numbers in `.py` files (`git grep -hoE '#[0-9]{3,4}' -- '*.py' | sort -u`), which makes the history of each guard traceable.
* Dependency sprawl is moderate in direct deps (16) but provider SDKs pull in the LangChain stack for four vendors (`pyproject.toml:13` to `:17`); nothing is upper pinned and there is no lockfile.

## 7. Maintenance

Commits by month (HEAD, `git log --format=%ad --date=format:%Y-%m`):

| Month | Commits | Month | Commits |
|---|---|---|---|
| 2024-12 | 12 | 2026-02 | 18 |
| 2025-01 | 6 | 2026-03 | 39 |
| 2025-02 | 1 | 2026-04 | 24 |
| 2025-06 | 29 | 2026-05 | 49 |
| 2025-07 | 4 | 2026-06 | 40 |
| 2025-09 | 10 | 2026-07 | 14 |
| 2025-10 | 6 | 2026-08 | 16 |
| 2026-01 | 5 | 2026-09 | 120 |

Six months had no commits (2025-03, 2025-04, 2025-05, 2025-08, 2025-11, 2025-12). Activity since 2026-02 is steady, with a burst of 120 in 2026-09 that includes a package reorganization and the v0.5.0 and v0.5.1 releases.

* Bus factor 1: 85.2% of all commits, 93.4% since 2026-03-01, under one name in two spellings. Next largest: Edward Sun 13, CadeYu 12, luohy15 10.
* Commit types: 147 subjects start with `fix`, 62 with `feat`; 34 merge commits. Highest PR number in subjects is #1402, so the tracker has at least that many issues and PRs combined.
* Issue signal from messages: recurring themes are look ahead leaks (15 commits), provider quirks (structured output, reasoning parameters per vendor), rate limits on Reddit and Yahoo, and CLI checkpoint resume that was a no op until #1249 (`graph/trading_graph.py:187` to `:191`).

## 8. Value for traderbot

**Rebuild in our own code (ranked):**
1. Date authority outside the model. Tools read the run date from graph state and clamp every model supplied date to it (`agents/tools.py:1` to `:5`; `date_window.py:72` to `:95`); windows are half open in UTC and undated items are dropped for past windows (`date_window.py:17` to `:30`); a feed that cannot observe a window says "unavailable", not "none found" (`date_window.py:38` to `:62`). This is the single most transferable idea for honest evals. Pair it with EDGAR as filed and FRED vintage pinning for fundamentals and macro (`sec_edgar.py:147` to `:185`; `fred.py:184` to `:193`).
2. Deferred settlement with a known by date. Decisions are logged pending, scored only after the holding window trades, and a lesson is visible to a later run only if its resolution date precedes that run's date (`graph/settlement.py:38` to `:132`; `decision_log.py:73` to `:110`). Rebuild on a typed store (SQLite rows, not a regex parsed markdown file), tag provenance, and never feed raw model text back as memory.
3. Staged adversarial review with a non tradeable sentinel. Bull and bear, a judge told that "conflict alone is not a reason to Hold" (`research_manager.py:36`), a trader that must state absolute entry and stop levels (`trader.py:58` to `:61`), a three stance risk review, then a final judge; unparseable output becomes `REVIEW` instead of Hold (`agents/rating.py:27` to `:31`). For traderbot: pass structured evidence rather than whole reports, randomize or alternate speaker order, cap transcripts, and treat the output as one signal behind deterministic position sizing and risk limits.
4. Vendor router with explicit chains and typed "no data" sentinels that tell the model not to invent numbers (`router.py:184` to `:291`); coercion of LLM numeric fields that drops percentages and ranges rather than failing the whole decision (`agents/schemas.py:30` to `:57`).

**Avoid:**
* Any historical score from this pipeline as evidence of edge; evaluate LLM signals only on dates after the model's training cutoff (forward or paper trading). This is the same failure as test set contamination in LLM evals: the backtest window is inside the training split.
* Default yfinance vendors for anything dated in the past (news starved, fundamentals leak through the filing lag, prices back adjusted). Use unadjusted prices plus a corporate action table, and next bar execution with costs.
* Untrusted text in system prompts, and model output recycled into later prompts without provenance.
* The serial single ticker graph for a universe: 15 or more calls and quadratic context per ticker per day is expensive for a signal of unproven value.
* The announcements phone home, floor only dependency pins, and the `.dockerignore` gap.

**License terms.** Apache 2.0 (`LICENSE`). Copying code is allowed with the license text, retained notices and a statement of changes, plus the patent grant and its termination clause. Per traderbot's own rule, rebuild the ideas in original code and cite this repo and the paper in the provenance record; no code needs to be copied to use any idea above.

## Unverified

* The paper's evaluation setup (tickers, dates, baselines, figures): the abstract page carries none, and the full PDF was not fetched.
* Whether `yf.Ticker.history` (`yahoo/market.py:39`, `:277`) defaults to adjusted prices in the yfinance version a fresh install resolves; `ohlcv.py:249` sets `auto_adjust=True` explicitly.
* Training cutoffs of `gpt-6-sol` and `gpt-6-luna`, their prices, and real token counts per run.
* The typical number of tool rounds per analyst, and so the typical LLM calls per run beyond the 15 minimum; nothing was run.
* Whether `yf.Search` ever returns articles old enough to populate global news for historical windows.
* Whether Alpha Vantage `NEWS_SENTIMENT` scores are computed at publish time or later.
* Whether the stdlib expat in a given runtime limits entity expansion for the Reddit feed, and whether the `langgraph-checkpoint` version resolved by the floor pins carries known serializer CVEs.
* TypeSafe's data retention terms and its relationship to the project.
* Whether `Yijia-Xiao` and `Yijia Xiao` are one person; open issue count on GitHub (only commit message references were visible offline).
* Exploitability of the decision log separator forgery against current models.
