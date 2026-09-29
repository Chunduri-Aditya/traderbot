# Audit: OpenBB (Open Data Platform)

Source: https://github.com/OpenBB-finance/OpenBB, cloned at `/Users/chunduri/Desktop/Projects/traderbot/repos/OpenBB`, branch `develop` at `3e071fcc2c` (2026-07-20). Static review only; nothing installed or run. Audit date 2026-09-28.

**Verdict**
1. A Python data access layer: 32 provider plugins (350 fetchers) behind one typed query model, exposed as a Python API, a FastAPI REST server, a CLI, and an MCP server. It has no execution, no streaming, and no order books.
2. Worth using as a research and backtest data layer if you install a small subset of providers. It is not a live trading feed.
3. Biggest caveat: `develop` is no longer where the project lives. The `v5` branch (183 non merge commits, active today) relicenses to Apache 2.0 and removes 15 of the 32 providers, including yfinance, FMP, Intrinio, Tiingo, Alpha Vantage and Tradier. Anything built on `develop` today pins you to a frozen AGPL line.

## Facts

| Item | Value | Source |
|---|---|---|
| License, `develop` | `AGPL-3.0-only`; 53 of 55 `pyproject.toml` declare it, 2 declare none | `LICENSE:3`, shell tally |
| License, `v5` | Apache 2.0 | commit `8a1b5fd93e` (2026-09-24), `origin/v5:LICENSE` |
| License conflict | `desktop/LICENSE` says MIT; `desktop/package.json:6` and `desktop/src-tauri/Cargo.toml:6` say `AGPL-3.0` | files |
| Commits | 6863 on `develop` (2020-12-20 to 2026-07-20); 7076 on `origin/v5` | `git rev-list --count` |
| Tracked files | 2191 (1295 py; 1034 non test py, 201875 lines) | `git ls-files` |
| Providers / fetchers | 32 / 350 | AST parse of each `Provider(...)` call |
| Providers with no key | 17 (155 fetchers) | same |
| Providers declaring a key | 15 (195 fetchers); 7 of them have some keyless fetchers | same |
| Standard models | 180 | `core/openbb_core/provider/standard_models` |
| Router commands | 281 | `@router.command` count |
| Extensions (routers) | 18 | `openbb_platform/extensions` |
| Test functions | 1682 total: 632 integration (live APIs), 443 provider, 332 core, 131 CLI | `git grep` count |
| HTTP cassettes | 329 YAML | `*/tests/record/` |
| REST API default | `127.0.0.1:6900`, auth off, CORS `*` | `platform_api/main.py:290,299`, `env.py:24`, `api_settings.py:11` |
| MCP server default | streamable HTTP on `127.0.0.1:8001`, auth off, CORS `*` with credentials | `app_import.py:234`, `settings.py:189,212`, `app.py:124-137` |
| Telemetry | none found in the Python platform | keyword sweep, section 4 |
| Top maintainer share | 161 of 207 `develop` commits since 2025-04-01 (0.778) | `git log` |

## 1. What it is

OpenBB calls it the "Open Data Platform (ODP)": an open source layer that "consolidates and exposes data to multiple surfaces at once: Python environments for quants, OpenBB Workspace and Excel for analysts, MCP servers for AI agents, and REST APIs" (`README.md:20`).

**Business model.** The open repo is the data plumbing. The paid product is OpenBB Workspace (`pro.openbb.co`), an enterprise UI that connects to a local ODP backend (`README.md:40-95`). The CLI still references `payments.openbb.co` and `my.openbb.co` as settings (`cli/openbb_cli/models/settings.py:34-35`). Open: core, providers, extensions, CLI, MCP server, desktop tray app. Closed: Workspace UI and its AI agents. The companion repos `backends-for-openbb` and `agents-for-openbb` are linked from the README but not audited here.

**License.**
- `develop` and `main`: `AGPL-3.0-only` for the whole repo (`LICENSE:3`: "All files in this repository are licensed under the GNU Affero General Public License v3.0"). 53 of 55 package manifests say `AGPL-3.0-only`. `congress_gov` and `multpl` declare no license field and fall back to the root file.
- `desktop/LICENSE` is MIT, while `desktop/package.json:6`, `desktop/src-tauri/Cargo.toml:6` and `desktop/src-tauri/tauri.conf.json:55` say AGPL. The license texts disagree inside one folder.
- `v5`: commit `8a1b5fd93e` "[V5] Update Repository License to Apache 2.0 (#7677)" rewrites `LICENSE`, `README.md`, and every remaining `pyproject.toml`. `origin/v5:LICENSE` line 3 reads "All files in this repository are licensed under the Apache License, Version 2.0." Release tags such as `openbb-core-v2.0.1` (2026-09-28) sit on `v5` and not on `develop`.
- No contributor license agreement is in the repo (0 hits for `CLA`, "contributor license", "license agreement", `signed-off-by`; positive control `AGPLv3` hit 1 file). How OpenBB relicensed code from 294 historical authors is not documented in the tree.

**Maturity.** Started 2020-12-20, 108 tags, per package semver (`openbb` 4.7.3, `openbb-core` 1.6.13 on `develop`; 2.0.x on `v5`). Heavy churn in provider inventory: Polygon was removed 2026-01-27 as "No Longer Exists and Can't Support" (`e0868c73d1`).

## 2. Architecture

**Plugin model.** Everything is a Python package discovered by entry points (`core/openbb_core/app/extension_loader.py:20-22`):
- `openbb_provider_extension`: a provider, for example `fmp = "openbb_fmp:fmp_provider"` (`providers/fmp/pyproject.toml:18-19`).
- `openbb_core_extension`: a router, for example `equity = "openbb_equity.equity_router:router"` (`extensions/equity/pyproject.toml:18-19`).
- `openbb_obbject_extension`: accessors and hooks on the result object (charting lives here).

**Provider contract.** A `Provider` holds a name, a credential list (prefixed with the provider name, `provider/abstract/provider.py:46-51`), and a `fetcher_dict` mapping standard model names to `Fetcher` classes. A `Fetcher[Q, R]` is a three stage pipeline, `transform_query` then `extract_data` (or `aextract_data`) then `transform_data` (`provider/abstract/fetcher.py:42-85`). Each provider subclasses a standard `QueryParams` and `Data` model (180 of them) and adds its own extra fields. `Fetcher.test` (`fetcher.py:115-200`) asserts the pipeline stages are distinct; the provider unit tests call it against recorded HTTP.

**Query flow** for `obb.equity.price.historical("AAPL", provider="yfinance")`:
1. `import openbb` runs `PackageBuilder(...).auto_build()` (`core/openbb/__init__.py:39`), which generates static Python wrappers for every installed router into the installed package and lints them with `ruff` in a subprocess (`app/static/utils/linters.py:61`). This is why `ruff` is a runtime dependency of `openbb-core` (`core/pyproject.toml`).
2. The generated method calls the router function, for example `historical` in `extensions/equity/openbb_equity/price/price_router.py:47-60`, which does `OBBject.from_query(Query(**locals()))`.
3. `Query.execute` merges standard params with provider extra params, warning on extras the chosen provider does not support (`app/query.py:36-80`).
4. `QueryExecutor.execute` looks up the provider in the registry, picks the fetcher by model name, filters credentials, and raises if a required key is missing (`provider/query_executor.py:19-97`).
5. The result is wrapped in `OBBject` with `results`, `provider`, `warnings`, `chart`, `extra` (`app/model/obbject.py:43-59`) and converters `to_dataframe`, `to_polars`, `to_numpy`, `to_dict`, `to_llm` (`obbject.py:121-337`).

A router command whose model no installed provider serves is dropped silently at load (`app/router.py:242-252`). Example: `obb.equity.price.nbbo` is declared (`price_router.py:33-44`) but 0 providers implement `EquityNBBO`.

**REST API.** `openbb_core/api/rest_api.py` mounts the same routers into FastAPI (`rest_api.py:74-86`). `openbb-api` (`extensions/platform_api`) adds Workspace specific `widgets.json`, `apps.json` and `agents.json` endpoints and launches Uvicorn.

**MCP server.** `openbb-mcp-server` (`extensions/mcp_server`) wraps the FastAPI app with `FastMCP.from_fastapi` (`app/app.py:527-532`), so every REST route becomes an MCP tool. It also ships four `SKILL.md` files that it serves to agents as MCP resources (`app/app.py:561-567`), and can optionally serve the host's local skill directories for Claude, Cursor, Codex, Gemini and others (`app/app.py:65-74,576-587`, `skills_providers` default None). Category based tool discovery exists but was switched off by default on 2026-04-03 (`18d1696243`; `settings.py:50-63`: `default_tool_categories=["all"]`, `enable_tool_discovery=False`). The README still claims it "keeps the initial tool list small" (`mcp_server/README.md:5`), which is no longer the default.

**Other surfaces.** `cli/` (the `openbb-cli` terminal app), `desktop/` (Tauri tray app, about half Rust and half TypeScript per `desktop/README.md`), `cookiecutter/` (extension scaffolding).

## 3. Data coverage for a trading bot

**Providers on `develop`** (fetcher counts and credential lists parsed from each `Provider(...)` call with `ast`; "keyless fetchers" counts `require_credentials = False` in the package):

| Provider | Fetchers | Key | Access mode | Evidence |
|---|---|---|---|---|
| yfinance | 29 | none | unofficial, wraps the `yfinance` library | `yfinance/pyproject.toml` dep; `utils/helpers.py:89` |
| cboe | 11 | none | unofficial public CDN JSON, delayed | `cboe/openbb_cboe/models/options_chains.py:76` (`cdn.cboe.com/api/global/delayed_quotes`) |
| tmx | 24 | none | unofficial GraphQL, random User Agent header | `tmx/openbb_tmx/utils/helpers.py:229,735` |
| finviz | 7 | none | scrape via `finvizfinance` | `finviz/openbb_finviz/__init__.py:14` ("Unofficial Finviz API") |
| multpl | 1 | none | HTML scrape (`pandas.read_html`) | `multpl/openbb_multpl/models/sp500_multiples.py:136` |
| seeking_alpha | 3 | none | unofficial internal API | `seeking_alpha/openbb_seeking_alpha/utils/helpers.py:28` |
| stockgrid | 1 | none | unofficial internal endpoint | `stockgrid/openbb_stockgrid/models/short_volume.py:62` |
| wsj | 3 | none | unofficial market data JSON | `wsj/openbb_wsj/models/losers.py:87` |
| sec | 24 | none | official EDGAR | hosts `www.sec.gov`, `xbrl.sec.gov` |
| federal_reserve | 13 | none | official | `federalreserve.gov`, `newyorkfed.org` |
| imf | 8 | none | official SDMX | `api.imf.org` |
| oecd | 9 | none | official SDMX | `sdmx.oecd.org` |
| ecb | 3 | none | official | `data.ecb.europa.eu` |
| famafrench | 6 | none | official data library | `mba.tuck.dartmouth.edu` |
| finra | 2 | none | official | `api.finra.org` |
| government_us | 6 | none | official, random User Agent header | `government_us/openbb_government_us/utils/helpers.py:3-6` |
| deribit | 5 | none | official public API | `deribit.com` |
| fmp | 69 | `api_key` | official, commercial | `financialmodelingprep.com` |
| intrinio | 38 | `api_key` | official, commercial | `api-v2.intrinio.com` |
| fred | 36 | `api_key` (1 keyless) | official | `api.stlouisfed.org` |
| nasdaq | 9 | `api_key` (7 keyless) | 7 fetchers hit unofficial `api.nasdaq.com` with random UA; key is for Nasdaq Data Link | `nasdaq/openbb_nasdaq/utils/helpers.py:15,35` |
| econdb | 8 | `api_key` (8 keyless) | official; fetches a temporary token when no key | `econdb/openbb_econdb/utils/helpers.py:395` |
| congress_gov | 8 | `api_key` (2 keyless) | official | `api.congress.gov` |
| tiingo | 7 | `token` | official, commercial | `api.tiingo.com` |
| tradier | 5 | `api_key`, `account_type` | official brokerage API | `api.tradier.com` |
| benzinga | 4 | `api_key` | official, commercial | `api.benzinga.com` |
| alpha_vantage | 3 | `api_key` | official | `alphavantage.co` |
| bls | 2 | `api_key` (1 keyless) | official | `api.bls.gov` |
| cftc | 2 | `app_token` (2 keyless) | official Socrata | `publicreporting.cftc.gov` |
| eia | 2 | `api_key` (1 keyless) | official | `api.eia.gov` |
| biztoc | 1 | `api_key` | via RapidAPI | `biztoc.p.rapidapi.com` |
| tradingeconomics | 1 | `api_key` | official, commercial | `api.tradingeconomics.com` |

Totals: 17 providers need no key (155 fetchers); 15 declare a key (195 fetchers), and 7 of those (bls, cftc, congress_gov, econdb, eia, fred, nasdaq) have at least one keyless fetcher. 8 of the 17 keyless providers are scrapers or unofficial endpoints (yfinance, cboe, tmx, finviz, multpl, seeking_alpha, stockgrid, wsj), plus the keyless half of nasdaq. Whether a declared key has a free tier is a vendor fact the repo does not state (see Unverified).

**Asset classes** (providers implementing each standard model, counted by script):

| Model | Providers |
|---|---|
| `EquityHistorical` | 8: alpha_vantage, cboe, fmp, intrinio, tiingo, tmx, tradier, yfinance |
| `EquityQuote` | 6: cboe, fmp, intrinio, tmx, tradier, yfinance |
| `OptionsChains` | 6: cboe, deribit, intrinio, tmx, tradier, yfinance |
| `CryptoHistorical` | 3: fmp, tiingo, yfinance |
| `CurrencyHistorical` | 3: fmp, tiingo, yfinance (ECB reference rates separately) |
| `FuturesHistorical` / `FuturesCurve` | 2 / 3: deribit, yfinance / cboe, deribit, yfinance |
| `IndexHistorical` | 4: cboe, fmp, intrinio, yfinance |
| `IncomeStatement` | 4: fmp, intrinio, sec, yfinance |
| `EquityShortInterest` | 1: finra |
| `EquityNBBO` | 0 |
| Macro, rates, yield curves | fred (36), federal_reserve (13), oecd, imf, ecb, econdb, bls, eia |

**Intraday vs daily.** Intraday intervals exist in the query models: yfinance down to `1m` (`yfinance/.../equity_historical.py:50-62`), fmp `1m` to `4h` (`fmp/.../equity_historical.py:32`), intrinio, tiingo, tradier, tmx, alpha_vantage. cboe intraday is "only available for the most recent trading day at 1 minute intervals" (`cboe/.../equity_historical.py:31-36`). How far back intraday goes is set by each vendor, not by OpenBB; yfinance defaults to one year back when no start date is given (`yfinance/.../equity_historical.py:125-126`).

**Historical depth.** Not enforced by OpenBB. Historical options chains exist only for intrinio (end of day, `intrinio/.../options_chains.py:60`) and tmx (`tmx/.../options_chains.py:27`). Historical index membership exists only for fmp (`historical` flag, `fmp/.../index_constituents.py:30-32`); the standard `IndexConstituents` model has no date field.

**Point in time.** The standard fundamentals models carry only `period_ending`, `fiscal_period`, `fiscal_year` (`standard_models/balance_sheet.py:31-35`); 0 standard models have an as of or filing date field. So by default you get the latest restated numbers keyed by period end, which is lookahead bias in a backtest. Two exceptions:
- fmp income statement returns `filing_date` and `accepted_date` (`fmp/openbb_fmp/models/income_statement.py:68-72`); intrinio, sec and yfinance return neither.
- The SEC provider has `pit_mode` (`sec/openbb_sec/models/income_statement.py:40-47`, same on balance sheet, cash flow and growth models) that keeps the first reported value per period instead of restated comparatives (`sec/openbb_sec/utils/statement_schema/_extraction.py:667-684`, earliest `filed` wins). It still does not put the filing date on the output rows, so you must join filing dates yourself to know when a number became knowable. The test suite references `pit_mode` on 1 line.

## 4. Security

**Credential storage.** Keys live in plaintext JSON at `~/.openbb_platform/user_settings.json` (`app/constants.py:6-7`), written with `Path.write_text` and no permission change (`app/service/user_service.py:39-49`), plus `~/.openbb_platform/.env` loaded by `dotenv` (`env.py:18`). The desktop app reads and writes the same file (`desktop/src-tauri/src/tauri_handlers/credentials.rs:15`); 0 permission calls in that file. No OS keychain for API keys.

Two sharp edges in `app/model/credentials.py`:
- `OBBSecretStr` serializes the real secret whenever the model is dumped to JSON (`credentials.py:30-35`, `PlainSerializer(lambda x: x.get_secret_value(), when_used="json-unless-none")`). The `SecretStr` wrapper only masks `repr`.
- Any environment variable whose name ends in `API_KEY` is pulled into OpenBB credentials, not only OpenBB's own keys (`credentials.py:146`). An `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in the environment becomes an OpenBB credential.

Combined, `GET /user/me` (`api/router/user.py:14-19`) returns user settings, credentials included, as JSON. That router is mounted only when `OPENBB_DEV_MODE` is true (`rest_api.py:77-78`). With dev mode on, auth off and CORS `*` (the defaults), any web page open in the same browser could read every key, including unrelated `*API_KEY` variables. I did not run this; confidence is high from the code path, medium on the exact serialization (see Unverified).

Logging is safer: startup logs record keys only as `defined` or `undefined` (`app/logs/logging_service.py:166-188`), handlers are limited to `stdout`, `stderr`, `noop`, `file` (`app/model/system_settings.py:97-103`), and argument values are truncated to 300 characters (`logging_service.py:260`).

**REST API.** Binds `127.0.0.1:6900` by default (`platform_api/main.py:290,299`). Auth is HTTP Basic behind `OPENBB_API_AUTH`, default false (`env.py:22-24`, `api/auth/user.py:12`); comparison uses `secrets.compare_digest` (`user.py:29-36`). CORS defaults to `*` for origins, methods and headers (`app/model/api_settings.py:11-13`). With auth off, any site the user visits can call the local API and spend the user's vendor quotas.

**MCP server.** Default transport is streamable HTTP (`mcp_server/utils/app_import.py:234`) on `127.0.0.1:8001` (`settings.py:189`), `server_auth` None (`settings.py:212-213`), and CORS middleware with the platform's `*` origins plus `allow_credentials=True` (`app/app.py:124-137`). The optional auth is a static base64 `user:pass` sent as a Bearer token (`app/auth.py:40-65`), which is Basic auth with a different header name. Tool outputs carry third party text (news, filings, transcripts) into the agent context with no filtering (0 relevant hits for sanitize, untrusted, or injection handling in `openbb_mcp_server`). For an agent that can also trade, that is a direct prompt injection path.

**Agent instruction files in the repo (recorded, not followed).** Four `SKILL.md` files under `extensions/mcp_server/openbb_mcp_server/skills/` are served to connected agents by default. `develop_extension/SKILL.md:3` tells the agent to "scaffold, implement, install, and verify" when a user asks for an app; the skills contain 13 `pip install` lines in total. No hidden or adversarial phrasing found. They are an instruction channel that the server controls and the client agent receives.

**Telemetry and phone home.** Nothing found in the Python platform. `posthog` 0 files, `mixpanel` 0, `segment.io` 0, `track_event` 0. `sentry` 8 hits, all inside recorded third party HTML in test cassettes or a notebook output blob. `amplitude` and `analytics` hits are data fields. The CLI has hub and payments URLs as settings (`cli/openbb_cli/models/settings.py:34-35`) and, on `--url`, downloads a routine script from a user supplied URL, forcing https (`cli/openbb_cli/controllers/cli_controller.py:353-378`). The desktop app auto updates from GitHub releases with a minisign public key (`desktop/src-tauri/tauri.conf.json:59-63`).

**Desktop certificate.** The desktop app can generate a self signed certificate with `BasicConstraints CA:TRUE` (`desktop/src-tauri/src/utils/certs.rs:225`), write its private key to disk (`certs.rs:119-125`), and install it as a trusted root (`certs.rs:260` Windows `Root` store, `certs.rs:289-292` macOS `trustRoot`). Its key usage omits `keyCertSign` (`certs.rs:228-231`), which should stop conforming validators from accepting certificates it signs. This path is opt in (`install_in_trust_store`). Not relevant if traderbot skips the desktop app.

**Other code risks.**
- Charting grid runs `eval` on a user typed pandas expression with an empty `__builtins__` and a token blocklist (`obbject_extensions/charting/openbb_charting/core/backend.py:265-311`). The blocklist does not include `pickle` or `read_`, so `pd.read_pickle(url)` looks reachable. That is a static reading, not tested; the input is local user input.
- `package_builder.py:1969-1980` `eval`s type and default strings during code generation.
- IMF loads a committed 23.5 MB pickle (`providers/imf/openbb_imf/utils/metadata.py:76`, `assets/imf_cache.pkl.gz`). I disassembled it with stdlib `pickletools` without unpickling: 0 `GLOBAL` or `STACK_GLOBAL` opcodes and 0 `REDUCE`, so at this revision it holds only dicts, lists and strings. A future update to that blob would not be reviewable in a diff.
- TLS verification is on by default; 0 hard coded `verify=False` (positive control matched). A `verify_ssl` setting can turn it off (`provider/utils/helpers.py:170-251`).

**Supply chain.**
- Every provider is a separate PyPI package with caret ranges (`openbb_platform/pyproject.toml`). 54 `poetry.lock` files exist but only pin dev installs, not what `pip install openbb` resolves.
- `openbb-core` pins `fastapi = "0.136.3"` exactly, other deps by range (`core/pyproject.toml`).
- Scraper deps with their own maintainers: `yfinance ^1.4.0`, `finvizfinance ^1.1.0`, `random-user-agent ^1.0.1` (used by nasdaq, tmx and government_us to rotate browser User Agent headers).
- Import time code generation: `import openbb` rewrites files under the installed package and shells out to `ruff` (`core/openbb/__init__.py:39`, `linters.py:61`). `OPENBB_AUTO_BUILD` defaults true (`env.py:42-44`).
- Third party `on_command_output` hooks are gated behind `OPENBB_ALLOW_ON_COMMAND_OUTPUT`, default false (`env.py:64-66`).
- CI: 19 of 78 `uses:` lines pinned to a SHA; first party `actions/*` are tag pinned. Unit tests run on `pull_request` (not `pull_request_target`) with `contents: read` (`.github/workflows/test-unit-platform.yml:3-18`). Pre commit runs `detect-private-key` and `detect-secrets` (`.pre-commit-config.yaml:12,86`).

**Secrets in the repo.** No live secrets found. Pattern sweep for AWS, OpenAI style, GitHub, Slack tokens and PEM private keys returned 0 (positive control matched). Cassette query parameters: 521 credential parameters, all `MOCK` placeholders except one server set `XSRF-TOKEN` cookie from 2024 in `providers/stockgrid/tests/record/http/test_stockgrid_fetchers/test_stockgrid_short_volume_fetcher_urllib3_v2.yaml:38` (a stockgrid session cookie, not a repo credential).

## 5. Code quality and tests

- **Tests.** 205 test files, 1682 test functions. Provider tests (443) replay recorded HTTP through `pytest-recorder` cassettes (329 YAML, 304 `record_http` or `vcr` marks), so they test parsing, not live endpoints. 632 integration test functions across 46 files call the live platform with real keys (for example `extensions/equity/integration/test_equity_python.py:12-21`). CI runs unit tests only, on Python 3.10 to 3.14 (`test-unit-platform.yml:23-24`); I found no workflow that runs integration tests.
- **Typing.** Pydantic v2 models throughout, 37 `py.typed` markers, mypy with `--check-untyped-defs` in pre commit and CI, but CI lints only changed files (`general-linting.yml:50,69-71`). 1735 `type: ignore` comments.
- **Standardization.** 180 standard models give a shared schema per data type, with provider fields layered on top. Field names and units still vary per provider in the extra fields.
- **Rate limiting.** Only FRED has a limiter (`providers/fred/openbb_fred/utils/rate_limiter.py`, added in `564eb18335`); 15 files mention rate limits. Retry or backoff logic appears in 4 non test files.
- **Doc drift.** README says Python "3.9.21 - 3.12" (`README.md:61`); package requires `>=3.10,<4` (`openbb_platform/pyproject.toml:15`); CI tests up to 3.14.

## 6. Maintenance

Commits per month (`develop` all commits; `v5` non merge commits not on `develop`):

| Month | develop | v5 |
|---|---|---|
| 2025-04 | 13 | 0 |
| 2025-05 | 14 | 0 |
| 2025-06 | 12 | 0 |
| 2025-07 | 18 | 0 |
| 2025-08 | 2 | 0 |
| 2025-09 | 14 | 0 |
| 2025-10 | 20 | 0 |
| 2025-11 | 9 | 0 |
| 2025-12 | 12 | 0 |
| 2026-01 | 14 | 0 |
| 2026-02 | 19 | 0 |
| 2026-03 | 23 | 0 |
| 2026-04 | 17 | 4 |
| 2026-05 | 8 | 28 |
| 2026-06 | 6 | 17 |
| 2026-07 | 6 | 22 |
| 2026-08 | 0 | 0 |
| 2026-09 | 0 | 112 |

**Why `develop` went quiet.** Work moved to the `v5` branch in the same repo, not to another repo or a closed product. `v5` has 183 non merge commits from 2026-04-28 to 2026-09-28, the latest 2026-09-28 22:27 (`477235eecc`). On 2026-09-26 to 2026-09-28 it published per package 2.x releases (`openbb-core-v2.0.1`, `openbb-mcp-server-v2.0.1`, `openbb-cli-v2.0.1` and others; 39 tags in 2026-09). `develop` received a backport "from v5 branch" on 2026-05-25 (`3735afca3f`). Neither the `develop` README nor the `v5` README states the move; the commit graph shows it.

**What v5 removed.** Providers present on `develop` and absent on `v5`: alpha_vantage, benzinga, biztoc, econdb, finviz, fmp, intrinio, multpl, seeking_alpha, stockgrid, tiingo, tradier, tradingeconomics, wsj, yfinance (15 removed), plus congress_gov folded into government_us (`1bace8672c`). Together they account for 188 of 350 fetchers. Removal commits carry no stated reason (for example `7a9ad1ecf1` "remove yfinance provider from repo", `59c7c7eff4` "remove fmp provider extension from repository"). `v5` adds jodi and expands keyless sources: nasdaq gains equity, crypto, ETF and Nordic historical fetchers; tmx gains currency and futures; ecb gains currency historical; deribit gains historical volatility (`git ls-tree origin/v5`). The `v5` metapackage manifest still depends on the old AGPL 1.x `openbb-yfinance`, `openbb-fmp`, `openbb-intrinio` and others from PyPI (`origin/v5:openbb_platform/pyproject.toml`), so the end state of the `openbb` metapackage is unsettled.

**Contributors.** 294 distinct author names on `develop` all time; 26 since 2025-04-01. One author, "Danglewood", made 161 of 207 `develop` commits since 2025-04-01 (0.778) and 163 of 183 `v5` commits; "deeleeramone" made 13 more on `v5`. Bus factor is about one.

**Stale branches.** `origin/feature/websockets` (119 non merge commits, last 2025-03-08) held a websockets extension for intrinio, polygon, tiingo and fmp; never merged, and Polygon has since been removed.

## 7. Value for traderbot

**As the data layer.** Useful for research and backtests if you install `openbb-core` plus a few providers rather than `openbb[all]`. The plugin model makes that straightforward: each provider is its own package. A reasonable subset: `openbb-sec` (fundamentals with `pit_mode`), `openbb-fred` and `openbb-federal-reserve` (macro and rates), `openbb-cboe` (delayed options chains and index data), `openbb-deribit` (crypto derivatives), plus one paid price source you already trust. yfinance and the other scrapers are fine for exploration and poor as a system of record: unofficial endpoints, rotating User Agent headers, no SLA.

**Depend vs rebuild.**
- Depend: SEC XBRL normalization (`providers/sec`, 41 py files, with a statement schema README) and the FRED and Federal Reserve fetchers. These are hard to rebuild and keyless or free.
- Rebuild: the `Fetcher` pattern (transform query, extract, transform data over pydantic models) is small, `fetcher.py` is 233 lines and most of that is a self test, and worth reimplementing in your own code rather than importing core, which brings FastAPI, Uvicorn, import time code generation and `ruff` as runtime dependencies.
- Skip: the REST server and MCP server for anything that trades. Both default to no auth and CORS `*`, and the MCP path feeds third party text into an agent unfiltered.

**License.**
- `develop` code and the published 1.x packages are `AGPL-3.0-only`. Running it privately inside your own bot creates no obligation. Offering a modified version to others over a network, or distributing it, triggers source release of the combined work. If traderbot could ever become a hosted product, avoid importing AGPL code.
- `v5` 2.x packages are Apache 2.0 and remove that constraint, but `v5` also drops the price providers most people would want. The old AGPL provider packages stay AGPL.
- Not legal advice; the lack of a visible CLA for the relicense is worth checking before relying on Apache terms for older code.

**Gaps a trading bot still fills itself.**
- Execution: 0 order placement code (`place_order`, `submit_order`, `create_order`, `/orders` all 0). Tradier is wired as a data source only.
- Streaming: no streaming API. Deribit opens a websocket only to snapshot an options chain with a 2 second timeout (`deribit/openbb_deribit/models/options_chains.py:156-160`); the websockets branch is abandoned.
- Order books: 0 L2 or depth fetchers; the NBBO command has 0 providers.
- Point in time: no as of dates on standard models; survivorship free universes only via fmp historical constituents; you own the knowledge time join.
- Rate limits, retries, caching policy per vendor: FRED only.
- Calendars and corporate action adjusted bar series: partial and per provider (`exchange-calendars` is a tmx dependency only).

## Unverified

- Whether `GET /user/me` actually returns plaintext keys: inferred from `OBBSecretStr` (`credentials.py:30-35`) plus FastAPI JSON serialization; not run.
- Whether FastMCP validates `Origin` or `Host` on the streamable HTTP transport (DNS rebinding). Not checked in the `fastmcp` source, which is outside this repo.
- Which directories the FastMCP vendor skill providers (`ClaudeSkillsProvider` and others) read, and so what `skills_providers` would expose.
- Free tier availability for keyed providers (FRED, BLS, EIA, congress.gov, CFTC and Nasdaq Data Link keys are free per vendor docs as I recall; FMP, Tiingo, Alpha Vantage freemium; Intrinio, Benzinga, Trading Economics paid). The repo does not state any of this.
- Intraday history depth per vendor and whether any provider returns delisted symbols (survivorship).
- Whether `pd.read_pickle` is reachable through the charting `eval` blocklist in practice.
- Whether "Danglewood" and "deeleeramone" are the same person.
- How OpenBB obtained rights to relicense contributions from 294 authors to Apache 2.0 (a CLA could exist outside the repo, for example a CLA bot).
- The final shape of the `v5` `openbb` metapackage: at the branch tip it is still version 4.7.3 and depends on removed 1.x provider packages; compatibility of those with `openbb-core` 2.x is unknown.
- `desktop` license: MIT file versus AGPL manifests; which one OpenBB intends.
