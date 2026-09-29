# Audit: `paperswithbacktest/awesome-systematic-trading`

Audited 2026-09-28 against commit `ddfee8b` (HEAD of `main`). Static review only: nothing in the repo was installed or executed.

**Verdict**
1. A curated link list (299 table rows in `README.md`) that doubles as the customer acquisition funnel for Papers With Backtest; it holds no trading engine, carries no license, and ships 61 legacy QuantConnect files derived from Quantpedia.
2. Worth using as an index for picking an engine, data and broker libraries (100 of 136 library rows are unflagged); not worth using as a source of strategy evidence or of code to copy.
3. Biggest caveat: the Strategies table shows the top Sharpe ratios picked from thousands of vendor replications, gross of costs, not excess of cash, with no multiple testing correction, and several crypto rows claim more test years than crypto has existed; treat every number as marketing until reproduced.

## Facts

| Item | Value | Source |
|---|---|---|
| Kind | Awesome list plus vendor funnel; no engine | file tree: 3 READMEs, `.gitignore`, 4 files in `scripts/`, 62 in `static/`, 2 in `.vscode/`, 1 workflow |
| Owner | GitHub org `paperswithbacktest`; maintainer Edouard d'Archimbaud | `git remote -v`; `git log` authors |
| Commits | 176 total, 156 by the maintainer (103 as `edarchimbaud`, 53 as full name, one email) | `git log --format='%an <%ae>'` |
| Distinct authors | 19 name and email identities, 18 emails | same |
| Last commit | 2026-09-28, `ddfee8b`, merge of PR #115 | `git log -1` |
| License | None. No LICENSE or COPYING file anywhere in history | `git log --all --name-only` grep for `licen\|copying`: 0 hits (control on `README`: 159) |
| English list rows | 299: 136 libraries, 61 strategies, 55 books, 22 videos, 14 blogs, 11 courses | counting script over `README.md` table rows |
| Library health flags | 36 of 136 flagged: 27 dormant, 6 archived, 3 gone | `grep -c` on the three tags |
| Rows pointing at the vendor | 64 of 299 (61 strategies, `pwb-toolbox`, own blog, own course) | `README.md:300`, `:400` to `:490`, `:634`, `:654` |
| Affiliate links | 23 amazon.fr links with `tag=darchimbaud-21`; 30 `amzn.to` short links | `grep -c` |
| Translation lag | Libraries: EN 136, ZH 108, JA 103 | counting script over each README |
| Commits in last 12 months | 45, all committed between 2026-08-01 and 2026-09-28 | `git log --since=2025-10-01` |

## 1. What it is

Purpose, in its own words: "a list of resources papers, softwares, books, articles for finding, developing, and running systematic trading" (`README.md:10`). The repo holds no library code. The only executable content is three maintainer scripts and 61 QuantConnect algorithm files kept as history (`README.md:494`).

Commercial interest is overt, and the list steers toward it:
* The first section after the title is a sales funnel: "Run your first paper with Papers With Backtest" (`README.md:13` to `:47`). A free account gets one strategy unlock, one clone and $1 of agent credit (`README.md:20` to `:22`); the paid "Backtester" plan adds 100 unlocks per month, datasets, $10 per month of agent credit and catalogue access over MCP (`README.md:41` to `:43`), with a pricing link (`README.md:44`).
* All 61 Strategies rows link to `paperswithbacktest.com/strategies/<slug>` (`README.md:400` to `:490`), where the code sits behind an account unlock (`README.md:35`). The Strategies section is a product catalogue, not a curated list.
* The vendor's own dataset loader `pwb-toolbox` sits in Data Sources (`README.md:300`, added in commit `2603fca`), its blog in Blogs (`README.md:634`), its course in Courses (`README.md:654`).
* Six English links carry UTM tags (`utm_campaign=awesome_systematic_trading`, `README.md:18`, `:28` to `:30`, `:44`, `:45`).
* 23 of 55 book links carry the amazon.fr associate tag `darchimbaud-21` (for example `README.md:549`), which matches the maintainer's surname. The 30 `amzn.to` links (for example `README.md:506`) are Amazon short links whose tag was not resolved.

The library section itself (`README.md:132` to `:385`) is mostly neutral third party open source.

## 2. Contents

### Categories and counts (English README, counted by script)

| Section | Rows | Section | Rows |
|---|---|---|---|
| Backtesting: event driven | 27 | Data Sources: general | 15 |
| Backtesting: vector based | 5 | Data Sources: crypto | 4 |
| Backtesting: crypto | 10 | Data Science | 10 |
| Trading bots | 10 | Databases | 5 |
| Indicators | 7 | Graph Computation | 7 |
| Metrics computation | 2 | Machine Learning | 10 |
| Optimization | 7 | TimeSeries Analysis | 4 |
| Pricing | 3 | Visualization | 4 |
| Risk | 1 | Broker APIs | 5 |
| Strategies (7 asset buckets) | 61 | Books (7 subsections) | 55 |
| Videos | 22 | Blogs | 14 |
| Courses | 11 | **Total** | **299** |

The README's own counters (136 libraries, 55 books, 22 videos, `README.md:52` to `:55`) match the script counts.

### `static/`

* `static/images/awesome-systematic-trading.jpeg`: banner.
* `static/strategies/`: 61 files, 9,464 lines. All 61 import `AlgorithmImports`, so they run only inside QuantConnect LEAN. 60 of 61 open with a `quantpedia.com/strategies/...` URL (for example `static/strategies/accrual-anomaly.py:1`), and 26 carry a "QC implementation changes" note, the format Quantpedia uses for its own QuantConnect ports. 6 import a `data_tools` module that is not in the repo (for example `static/strategies/crude-oil-predicts-equity-returns.py:299`), and 24 pull CSVs from `data.quantpedia.com/backtesting_data/` (for example `static/strategies/crude-oil-predicts-equity-returns.py:85`). `static/strategies/pairs-trading-with-stocks` lacks a `.py` extension. The header comments are usable as plain language strategy specs; the code is unlicensed and appears to be Quantpedia derived, so do not copy it.

### `scripts/`

* `scripts/check_links.py`: calls the GitHub API for every GitHub link in `README.md` and classifies each as missing, archived, dormant (no push for 730 days, `:22`) or ok. Standard library only.
* `scripts/update_counts.py`: recounts table rows per section in all three READMEs and rewrites the counters. Standard library only.
* `scripts/build_strategies_table.py`: builds the Strategies section from the vendor's gated Hugging Face dataset `paperswithbacktest/Strategies-NAV` (`:187` to `:199`).
* `scripts/paper_meta.json`: 3,806 slug to title and market entries used to label the table. It includes pipeline stage keys `__01_download_papers__`, `__03_metadata_extraction__`, `__04_improve_code__` (`scripts/paper_meta.json:2`, `:6`, `:10`) and a markets value of `**Datasets:**` (`:10419`), which point to automated extraction rather than hand curation.

### Where the Strategies numbers come from

Method, from `scripts/build_strategies_table.py`:
* Input is one NAV series per replication from the vendor dataset. The repo cannot reproduce it without a vendor token (`:179` to `:182`).
* Flat leading and trailing stretches are trimmed (`:98` to `:104`); series under 252 rows are dropped (`:112`).
* Sharpe is `mean / std * sqrt(252)` of simple daily returns (`:118`). No risk free rate is subtracted.
* "Years tested" is row count divided by 252 (`:117`); t is `Sharpe * sqrt(years)` (`:124`), an iid t with no autocorrelation adjustment.
* Filters: t above 1.96, at least 10 years, volatility between 1% and 100%, metadata present (`:208` to `:215`).
* Display: the 12 highest Sharpe rows per asset bucket (`:154`).

In sample or out of sample: neither in a clean sense. Each replication runs "over its own full history" (`README.md:61`, `:390`), a window that spans the paper's original sample and whatever came after, with no train and test split in the script. 39 of 61 rows show 36 or more years tested and the median is 37, so most windows cover the whole dataset span rather than the paper's sample. The numbers are vendor backtests, not figures quoted from the papers. Costs are excluded, and the caveat says so (`README.md:394`).

Problems with the numbers:
* **Selection on the displayed metric.** The table shows the 61 highest Sharpe rows out of 1,687 survivors (`README.md:394`) from 4,843 replications (`README.md:61`). At a one sided 2.5% level, about 121 of 4,843 null strategies clear t 1.96 by chance (computed). The shown median Sharpe is 1.06 against a catalogue median of 0.37 (`README.md:64`). This is a winner's curse leaderboard with no deflated Sharpe or multiple testing threshold.
* **Repeated papers.** 132 titles map to two or more slugs in `scripts/paper_meta.json` (285 slugs), for example `a_model_of_momentum` and `a_model_of_momentum_1`, and the table itself links a suffixed variant (`README.md:401`, `the-investment-capm-1`). Picking the best of several variants of one paper widens the selection effect.
* **Not excess of cash.** 13 of 61 rows run under 5% volatility, with implied mean returns of 1.1% to 8.0% per year (Sharpe times volatility, computed). If those NAVs accrue interest on collateral, part of each Sharpe is the cash rate. The repo does not say whether they do.
* **Impossible test lengths.** Crypto rows claim 34 and 35 years tested (`README.md:454`, `:455`, `:456`, `:458`) and 22 to 23 years (`:451`, `:457`). Crypto trades 365 days a year while the script divides by 252, so 16 years of daily bitcoin data reads as about 23 years; that fits `:451` but cannot explain 35. Those NAVs either are not pure crypto or are padded.
* **Papers that describe no strategy.** Top rows include a paper on data errors in German equity data (`README.md:402`), an analytical Kelly criterion solution (`:410`), betting on a biased coin (`:467`), operational risk theory (`:469`), annuity demand (`:479`, `:480`) and consumption theory (`:484`). A "replication" of these has to invent the trading rule, which fits the automated pipeline evidence above.
* **Metadata errors.** Truncated titles (`README.md:443`, `:483`, `:488`); currency papers filed under Derivatives (`README.md:464`, `:471`, `:473`); a truncated slug `the_time_varying_systematic_risk_of` tagged Currencies next to its full slug tagged Derivatives (`scripts/paper_meta.json:13742`, `:13746`).
* **Unreproducible headline claims.** Median Sharpe 0.37, 48% significant, beta 0.17, and "no measurable decay after publication" across 2,838 papers (`README.md:61` to `:73`) are static prose. No script in the repo produces them (`grep` for `4,843` and `2,838` in `scripts/*.py`: exit 1). The method lives on the vendor wiki (`README.md:75`). The no decay claim contradicts the published literature on post publication decay (see Unverified).

## 3. Quality

### Link rot sample (10 links, `curl -sIL`, status only)

| Link | Result |
|---|---|
| github.com/twopirllc/pandas-ta (`README.md:226`) | 404, matches its `no longer available` tag |
| github.com/alpacahq/marketstore (`README.md:334`) | 404, matches its tag |
| aaaquants.com/category/blog/ (`README.md:633`) | 406 after one redirect; inconclusive (HEAD refused) |
| blackarbs.com/blog/ (`README.md:635`) | 200 after two redirects |
| quantsportal.com/blog-page/ (`README.md:643`) | no response (connection failed) |
| hardikp.com (`README.md:636`) | 200 |
| Udacity AI for Trading (`README.md:662`) | 200 after one redirect; landing page not checked |
| Udacity ML for Trading (`README.md:663`) | 200 after one redirect; landing page not checked |
| Coursera guided tour (`README.md:656`) | 200 |
| cfte.education (`README.md:653`) | 200 after one redirect |

Two of ten are dead, one fails to connect, one is inconclusive, and the redirected 200s may land on catalogue pages rather than the course.

### Staleness and the link checker's reach

* 36 of 136 library rows are flagged, among them zipline (`README.md:145`), backtrader (`:146`), pyfolio (`:265`), ib_insync archived (`:274`), `pandas-ta` gone (`:226`). They remain listed with a tag rather than being removed.
* The tags are frozen text. `check_links.py --annotate` wrote them in commit `9ee0049` (2026-08-26) when the list had 103 GitHub entries; the monthly workflow runs without `--annotate` (`.github/workflows/link-check.yml:25`), so tags only change by hand.
* The checker reads only `README.md` (`scripts/check_links.py:21`) and only GitHub URLs (`:23`). 161 of 299 rows are non GitHub (Amazon, YouTube, blogs, courses, vendor pages) and are never checked. Its regex also skips GitHub URLs with a path or an org only link (`README.md:655`, `:660`).
* Quantopian webinar channel (`README.md:607`) and Quandl (`README.md:295`, archived) point at defunct services.

### Duplicates and errors

* The same Coursera course appears twice (`README.md:659`, `:661`).
* Broken nested link in the `go-tart` row (`README.md:225`, `[ta-lib]((https://...`).
* ThetaGang's badge alt says TypeScript, label says Python (`README.md:207`); LEAN is tagged Python only (`README.md:148`).
* "Hudson and Thames Quantitative Research" under Courses is a GitHub org, not a course (`README.md:660`).
* "ordered by descending populatrity (number of stars)" (`README.md:134`): typo, and the order cannot be checked statically because star counts are live badges.

### Self submitted entries

Five entries were added through PRs from the same GitHub account that owns the listed repo, all merged on 2026-08-27: Intelligent Trading Bot (`README.md:210`, PR #11 from asavinov), `PRISM-INSIGHT` (`:214`, PR #21 from dragon1086), QTradeX (`:177`, PR #17 from `squidKid-deluxe`), `ml-quant-trading` (`:181`, PR #72 from `initial-d`), TradeSight (`:213`, PR #33 from `rmbell09-lang`, commit authored by an identity named "Lucky AI"). Self submission is normal for awesome lists, but these rows carry the author's own marketing copy.

### Missing major tools (unverified, `grep -ci` in `README.md` returned 0 for each)

`zipline-reloaded` (maintained zipline fork), ib_async (maintained ib_insync fork), Alpaca SDK, Databento, Polygon, Tiingo, Alpha Vantage, fredapi, alphalens, empyrical, Polars, `python-binance`, OANDA, `schwab-py`, and the companion list `awesome-quant`. The Broker APIs section has 5 rows: one mainstream and live (ccxt), one archived, two dormant.

### Misleading performance claims

See section 2. Summary: `README.md:53` promises "the Sharpe ratio each one produced when it was coded and run", which is true in a narrow sense but omits that the rows are the top of a selection from thousands, not excess of cash, and computed on vendor NAVs the reader cannot inspect without an account.

## 4. Security and supply chain

Nothing in the repo is dangerous to read. Nothing in it is worth running for traderbot.

* **`.github/workflows/link-check.yml`**: triggers only on a monthly cron and manual dispatch (`:3` to `:6`), no `pull_request` or `pull_request_target`, so outside PRs cannot run code in CI. Permissions are `contents: read` (`:8` to `:9`). Passes `GITHUB_TOKEN` to `check_links.py` (`:21` to `:22`). Actions are pinned to major tags, not SHAs (`:15`, `:16`): a minor gap for the maintainer, no exposure for a reader. The `${{ }}` interpolation at `:50` carries only numeric exit codes.
* **`scripts/check_links.py`**: standard library; sends the token only to `api.github.com` (`:28` to `:32`); `--annotate` rewrites `README.md` in place (`:76` to `:84`).
* **`scripts/update_counts.py`**: standard library; rewrites three READMEs unless `--check` (`:115` to `:116`).
* **`scripts/build_strategies_table.py`**: the one script with real exposure if run. It needs a vendor Hugging Face token (`:179`), downloads gated vendor parquet files and parses them with pandas (`:196` to `:199`), imports numpy, pandas and huggingface_hub with no pinned requirements file anywhere in the repo, reads `scripts/paper_meta.json` by a path relative to the working directory (`:203`), and overwrites all three READMEs (`:225` to `:227`). Parsing vendor parquet on an old pyarrow carries a known deserialization risk (see Unverified). Do not run it.
* **`.vscode/`**: points the interpreter at `~/miniconda3/bin/python` (`.vscode/launch.json:11`, `.vscode/settings.json:3`). No `tasks.json`, so nothing runs on folder open.
* **`static/strategies/`**: a scan of all 61 files plus the 3 scripts for `eval`, `exec`, `compile`, `open`, `subprocess`, `os.system`, `requests`, `urllib`, `socket`, `pickle`, `base64`, `ctypes` in code (comments stripped) found hits only in `check_links.py` (urllib and `re.compile`); controls fired. At LEAN runtime, 24 files fetch third party CSVs from `data.quantpedia.com`: data under someone else's control, not code.
* **Secrets**: none in the tree; none in the patch history of all 10 remote branches for Hugging Face, GitHub, AWS, OpenAI style, Slack or PEM patterns (positive control matched).
* **Hidden content**: zero zero width, bidi or tag characters across all 72 text files (controls matched). The only HTML comments are the generator markers (`README.md:392`, `:492`) and `omit in toc`.
* **Instructions aimed at the reader**: `README.md:35` to `:45` tell the reader to create an account, clone a strategy and connect "your own Claude, Codex or Cursor over MCP" to the vendor catalogue. This is marketing aimed at humans, not an injection aimed at agents; it was not acted on. If traderbot ever connects that MCP server, its tool output is vendor controlled text entering the agent's context and gets the same untrusted input handling as any scraped page.
* **Tracking**: UTM parameters on 6 English links (and per language variants in the translations), Amazon associate tags, and two `bit.ly` links that hide their targets (`README.md:640`, `:642`).
* **PR #115 (`codex/github-acquisition-path`)**: three commits by the maintainer (`d198cb5`, `8cd1e0b`, `681e818`), 106 lines added and 12 removed across the three READMEs only. It replaces an HTML promo box with the funnel block described in section 1, adds the UTM tagged links, the free tier terms, the paid plan pitch and the MCP link. No code, workflow or script change. The branch name and the word "acquisition" mark it as growth work.
* **AI assisted maintenance**: 14 commits carry a `Co-Authored-By: Claude` trailer and 2 are authored by AI identities (`Claude`, `Lucky AI`). In the last 12 months, 15 of 27 non merge commits carry one or the other; PR #115 came from a `codex/` branch.

## 5. Maintenance

Commits by committer month, last 12 months:

| Month | Commits | Month | Commits |
|---|---|---|---|
| 2025-10 | 0 | 2026-04 | 0 |
| 2025-11 | 0 | 2026-05 | 0 |
| 2025-12 | 0 | 2026-06 | 0 |
| 2026-01 | 0 | 2026-07 | 0 |
| 2026-02 | 0 | 2026-08 | 37 |
| 2026-03 | 0 | 2026-09 | 8 |

By year: 101 in 2022, 17 in 2023, 10 in 2024, 3 in 2025, 45 in 2026. No commits between 2025-03-09 (`a975063`) and 2026-08-01. 35 commits landed on 2026-08-27 alone, when a backlog of outside PRs going back to #11 was merged in one sitting.

Contributors: 19 author identities; the maintainer holds 156 of 176 commits. Everyone else has one or two commits each.

Nature of recent work: three streams. Curation (merging old outside PRs, adding 24 libraries taken from the other awesome list of the same name, commit `ef78586`). Vendor tooling (the Strategies generator, counters, link check). Marketing (PR #115, `pwb-toolbox`, the funnel). Most of it is AI assisted. The pattern reads as a revived asset feeding a product launch, not steady community upkeep.

## 6. Value for traderbot

Status for each pick comes from the README's own tags and line numbers; none was checked against its live repo in this audit.

| Pick | Line | Why follow up |
|---|---|---|
| nautilus_trader | `README.md:154` | Event driven engine where the same strategy code runs in backtest and live; strongest candidate to build on |
| QuantConnect LEAN | `README.md:148` | Mature engine with broker and data adapters; also the runtime the 61 `static/strategies` specs target |
| pysystemtrade | `README.md:179` | A complete production futures system from Carver's books; reference design for forecast scaling, volatility targeting and position sizing |
| vectorbt | `README.md:178` | Fast vectorized parameter sweeps for the research loop, upstream of an event driven engine |
| PyBroker | `README.md:157` | Walk forward analysis and bootstrap significance tests built in: the eval discipline the Strategies table lacks |
| ccxt | `README.md:273` | Unified exchange API if traderbot trades crypto |
| Freqtrade | `README.md:188` | Complete crypto bot with dry run, backtest and remote control; reference for bot operations |
| HFTBacktest | `README.md:156` | Latency and queue position modeling if the bot goes intraday |
| skfolio | `README.md:245` | Portfolio construction with scikit learn style cross validation |
| edgartools, ArcticDB | `README.md:294`, `:336` | SEC fundamentals source and a tick and bar store for the data layer |

Gaps the list does not cover for a bot: a live broker for equities (ib_insync is archived at `README.md:274`; its fork and Alpaca are absent) and paid tick data vendors. Fill these from vendor docs, not from this list.

Cross domain note: the Strategies table fails the same way an eval leaderboard does when many runs are scored and the best is reported. The fix is the same too: count the trials, deflate the metric, hold out a period nobody tuned on. Build traderbot's backtest harness like an eval harness from the start.

## Unverified

* Status of every pick in section 6 as of today; the README tags date from 2026-08-26 (old rows) and 2026-09-03 (the 24 added rows, per commit `ef78586`).
* Whether the 30 `amzn.to` links carry the same associate tag; links were not resolved.
* Whether the vendor NAVs include interest on collateral, which decides how much of each low volatility Sharpe is the cash rate.
* Whether `static/strategies` code is copied from Quantpedia or only modeled on it; the evidence is the header URLs and the "QC implementation changes" format.
* The published post publication decay result (McLean and Pontiff, about a third to a half of anomaly returns lost after publication) is from memory; checking it would settle the `README.md:71` claim.
* A multiple testing threshold near t 3.0 (Harvey, Liu and Zhu) and the deflated Sharpe ratio (Bailey and López de Prado): from memory.
* pyarrow deserialization vulnerability `CVE-2023-47248` (fixed in pyarrow 14.0.1): from memory.
* LEAN's core language (C#), pysystemtrade's Interactive Brokers integration, Freqtrade's Telegram control: from memory.
* The missing tools in section 3 exist and are maintained; only their absence from the README was checked.
* Final landing pages of the redirected 200 responses in the link sample.
