# traderbot checklist pass (cloud session)

Give this file to a cloud session on `Chunduri-Aditya/traderbot`, after the Phase A inspection or with it. It turns Aditya's checklists into rows you run and mark. His harness (`~/.claude`) is not in this clone, so every rule you need is in this file.

## 0. What you check (VERIFIED on 2026-11-03 at `63049c1`, on Aditya's Mac)
- traderbot is a Python 3.13 CLI (`uv run traderbot ...`). It has no web server, no browser UI and no database server. State lives in SQLite at `~/.traderbot/ledger.sqlite`.
- Flow: `propose` asks a `claude` CLI subprocess (the desk) for a card. `gate.py` checks the card against fixed limits. Aditya runs `approve` in a terminal, and a paper order goes to Alpaca.
- Phases 0 to 2 are built (`PHASES.md`). Six modules are stubs that raise `NotImplementedError`: `backtest.py`, `cache.py`, `fence.py`, `fundamentals.py`, `scoring.py`, `live_gate.py`. Do not describe them as built.
- Baseline: `./scripts/check.sh` printed `ruff=0`, `fmt=0`, `mypy=0`, `pytest=0` (137 passed, 2 deselected). `scripts/mutate.py` printed `rows=27 red=27 not_red=0` and `gate_restored=clean`.
- One known OPEN row: `pip-audit` reports `certifi 2026.7.22`, advisory `PYSEC-2026-4320`, fixed in `2026.10.18`.

## 1. What this clone does not have
| Missing | Effect |
|---|---|
| Alpaca keys (macOS Keychain, read by `/usr/bin/security` in `keychain.py`) | `-m live` tests and `propose` are BLOCKED |
| A real terminal (`/dev/tty`) | `approve` and `reset-breaker` cannot run, and must not |
| `~/.traderbot/` | no real ledger; tests use `tmp_path` |
| The spec and the runbook named at the top of `PHASES.md` | Phase 3 to 7 details are TBD: ask Aditya |
| `~/.claude` agents, skills and rules | this file carries the rules you need |

## 2. Hard stops
If a row needs one of these, mark it BLOCKED and name the unblock.
- Do not run `traderbot approve`, `traderbot reset-breaker`, `traderbot propose` or `pytest -m live`. They spend money, place a paper order or need a terminal.
- Do not read, print or commit a key. Do not write to `~/.traderbot`.
- Do not change `Limits` or a reason code in `gate.py`. Do not weaken `broker.assert_paper`, `paper=True` or the deny rules in `.claude/settings.json`.
- Do not push, force push or rewrite history. Do not install a tool outside `uv` unless Aditya says yes.
- Run `scripts/mutate.py` only with `--scratch` outside the repo, and only when nothing else reads `gate.py`. After the run, `git status --short` must be empty.
- To break a guard outside `gate.py`, copy the repo to a temp directory and break it there. Never restore with `git checkout <file>` or `git stash`.
- Research subagents (read, search, inventory) run with `model: "sonnet"`. Code changes stay serial in the main loop.
- This pass writes one file: `docs/CHECKLIST_RESULTS.md`. Each fix waits for Aditya's approval and lands in its own commit, test first.

## 3. How to mark a row
- Verdicts: PASS, FAIL, OPEN (known, not fixed), N/A (one reason), BLOCKED (the exact unblock). Do not leave a row out.
- Evidence: the command, its raw output, and its exit code on its own line. Run `echo "exit=$?"` right after the command, never after a pipe.
- Tag each claim VERIFIED (you ran it), INFERRED (you read it in code), PROPOSED or UNTESTED.
- A guard is proven only by a deny test and an allow test. The deny input crosses the limit. The allow input sits on the limit. A guard with one half is FAIL.
- Take expected values from `RULES.md`, `PHASES.md` or `ARCHITECTURE.md`, never from the code under test.
- If output is empty or surprising, stop. Check the path, the match and the exit code before you use it.
- Before you trust a grep that finds nothing, run the same pattern on a string that must match.

## 4. Checklists

### A. Definition of done
Run A1 first. It is the unmutated control for A3.

| Id | Row | Command | Pass means |
|---|---|---|---|
| A1 | Lint, format, types, tests | `uv sync` then `./scripts/check.sh` | `ruff=0`, `fmt=0`, `mypy=0`, `pytest=0` |
| A2 | Interpreter | `uv run python -c "import sys; print(sys.version)"` | the version in `.python-version` |
| A3 | Mutation | `uv run python scripts/mutate.py --scratch "$(mktemp -d)"` | `not_red=0` and `gate_restored=clean` |
| A4 | Live path | `uv run pytest -m live tests/test_smoke.py` | BLOCKED: needs the Keychain keys on Aditya's Mac |
| A5 | Tree | `git status --short --untracked-files=all` | only `docs/CHECKLIST_RESULTS.md` |

### B. Project rules (`RULES.md`)
Quote the rule line and the code line for each row.

| Id | Rule | How to check |
|---|---|---|
| B1 | Money is `Decimal`; float only at the `alpaca-py` request boundary | `grep -n "float(" src/traderbot/*.py`; each hit is an order request field |
| B2 | One "now" per command in `America/New_York`; approve reads the clock again before submit | the B2 command below; trace each caller |
| B3 | Each gate rule has a reason code, a deny test, an allow test and a `mutate.py` row | list the codes in `gate.py`; match each one in `tests/test_gate.py` and in `ROWS` |
| B4 | A card built by code carries a `desk_label` other than `single` | `grep -n desk_label src/traderbot/*.py` |
| B5 | Every trading client call goes through `broker.assert_paper`, which raises | read `src/traderbot/broker.py`; no bare `assert` |
| B6 | Tests set `TRADERBOT_HOME` to `tmp_path` | read `tests/conftest.py` |
| B7 | The model never sets qty, price, order type or time in force | read the desk schema in `desk.py` and the sizing in `proposal.py` |
| B8 | No `skip` in `tests/test_smoke.py` | `grep -n skip tests/test_smoke.py` finds nothing |
| B9 | A deliberate stop is `cli.Refused`: one stderr line, exit 1 | read `cli.py`; find the tests for each stop named in `RULES.md` |
| B10 | Any desk failure is a REVIEW card, charged at the call cap when the cost is missing | read `desk.py` and `tests/test_desk.py` |
| B11 | The only runtime dependency is `alpaca-py==0.44.0` | read `pyproject.toml` |

B2 command:
```bash
grep -nE "datetime.now|time.time|date.today|utcnow" src/traderbot/*.py
```

### C. Trading correctness
| Id | Row | How to check |
|---|---|---|
| C1 | Paper only | `paper=True` at every client; a test proves a live client is refused |
| C2 | Stale bars fail closed; the newest bar counts only 16 minutes after the close | the `STALE_BARS` deny and allow tests; read `data.py` |
| C3 | No duplicate orders | `OPEN_ORDER_EXISTS`, first decision wins, the client order id lookup in `broker.py`; a test approves the same card twice |
| C4 | No look ahead | the desk and the gate see only bars closed before "now"; read `data.py` and the desk prompt |
| C5 | Time zones | no naive `datetime`, no `utcnow`; every stored time is aware |
| C6 | Each limit in `RULES.md` Constraints maps to a reason code with a boundary test | $1,000 notional, 0.5% band, $2.00 day spend, $0.25 per call reserved before the call, 10% position, 3 orders a day, breaker at 3 settled losses or 10% below the peak |
| C7 | Kill switch | `traderbot off` stops `propose` and `approve`; find the tests |
| C8 | A failed broker read refuses; it never assumes a state | read `status` and `approve` paths in `cli.py` and `broker.py` |
| C9 | Ledger events are append only and redacted | read `ledger.py` and `tests/test_ledger.py` |
| C10 | Fees, slippage and spread | N/A: `backtest.py` is a stub. Required at Phase 6 |

### D. Security
Commands for D1, D2 and D8, run from the repo root in one shell:
```bash
T="$(mktemp -d)"
P='AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9_-]{20,}|PK[A-Z0-9]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY'
echo "sk-$(printf '%024d' 0)" | grep -cE "$P"        # positive control: prints 1
git grep -nIE "$P"; echo "d1=$?"                      # D1: d1=1 means no hit
git log -p --all > "$T/hist.txt"; echo "log=$?"
grep -cE "$P" "$T/hist.txt"; echo "d2=$?"             # D2: prints 0, then d2=1
uv export --format requirements-txt --no-hashes --no-emit-project > "$T/reqs.txt"; echo "export=$?"
uvx pip-audit -r "$T/reqs.txt" --no-deps --disable-pip; echo "d8=$?"
```

Rows that apply:

| Id | Row | How to check |
|---|---|---|
| D1 | No secret in tracked files | `d1=1` and no hit lines |
| D2 | No secret in history | count 0 and `d2=1`, after the positive control printed 1. Run `gitleaks git` only if it is already installed |
| D3 | A key never reaches an error, a log or stdout | read `keychain.py` (the timeout path drops the exception with `from None`) and every log call |
| D4 | Subprocess calls use an argument list, no `shell=True`, a fixed executable, a timeout and a trimmed env | `desk.py` (`_run_claude`, process group kill) and `keychain.py`. Note whether `CLAUDE_BIN = "claude"` resolves through `PATH` |
| D5 | Model output is untrusted | the desk JSON passes a schema allowlist; desk text cannot reach a shell, an order field or the ledger unvalidated; desk text is escaped before the terminal prints it |
| D6 | SQL uses bound parameters only | read every `execute` in `ledger.py`; no f string or `+` near SQL |
| D7 | CLI input is validated | symbol charset and length, card prefix length; a bad prefix is `cli.Refused` |
| D8 | Vulnerable dependencies | the `pip-audit` line above. `certifi` is the known OPEN row. Its fix is a lock bump in its own commit after the tests pass, with Aditya's yes |
| D9 | Unused dependencies | each package in `pyproject.toml` has an import in `src/` or `tests/` |
| D10 | Spend and rate limits | the $2.00 day cap, the $0.25 reservation before each desk call, 3 orders a day; deny and allow tests for each |
| D11 | Agent deny rules exist | `.claude/settings.json` denies `approve` and the Keychain read. They bind Claude Code only, not a person |

N/A once, one reason for all: authentication, password hashing, session cookies, CSRF, password reset, admin routes, client bundle secrets, debug pages, exposed files on a host, Row Level Security, payments, XSS, uploads, HTTPS, security headers and CORS. traderbot has no web server, no user accounts and no browser surface. If a later phase adds one, these rows come back.

### E. Python
| Id | Row | How to check |
|---|---|---|
| E1 | Type hints on public signatures; strict mypy clean | A1 |
| E2 | No bare `except:` and no broad `except Exception` that swallows | `grep -nE "except( Exception)?:" src/traderbot/*.py` |
| E3 | `logging`, not `print`, outside the CLI views | `grep -n "print(" src/traderbot/*.py`; `cli.py` view output is allowed |
| E4 | Retry only for a named transient error (429, timeout) | read every retry or loop around a network call |
| E5 | Docstrings on public modules, classes and functions | read each module |

### F. Proof the tests can fail
| Id | Row | How to check |
|---|---|---|
| F1 | Each mutation row went red on its own named test | read the `RED` lines from A3 |
| F2 | Guards outside `gate.py` go red when broken | in a temp copy of the repo, break one at a time: `assert_paper`, the approve card lookup, the kill switch, the spend reservation, the breaker latch. Run its test, see red, discard the copy |
| F3 | Every deny test has its allow test | pair them by name in `tests/test_gate.py` and the attack test files |

### G. Backtest and research hygiene
N/A at `63049c1`: `backtest.py` and `scoring.py` are stubs. Record these rows as acceptance criteria for Phases 3, 4 and 6:
- Split by time; fit only on train; a test asserts the split sets do not overlap; read the test split once, at the end.
- Set and record every seed; a result counts only when it reproduces.
- Model fees, slippage and spread.
- Compare against buy and hold SPY and the rules baseline; the baseline equals hand computed values.
- Report n, mean and spread, never a single draw; Brier score and bootstrap CI.
- Point in time data: EDGAR facts satisfy `filed <= as_of`; the watchlist is as of its date, to avoid survivorship bias.
- Pin the desk model snapshot in code and in each result record. Check now whether `desk.py` already does this.

### H. Speed
N/A once for the UI, asset, rendering, CDN, database server and infrastructure rows: traderbot has no browser surface and no server. Two network rows apply to any client:

| Id | Row | How to check |
|---|---|---|
| H1 | Every external call has a timeout and a failure path | desk subprocess (300 s, group kill), Keychain read, every `alpaca-py` call (find whether a timeout is set) |
| H2 | No duplicate calls | one Alpaca read per resource per command; `approve` submits once |

## 5. Report
Write `docs/CHECKLIST_RESULTS.md`:
1. One header line: commit sha, date, Python version, the four `check.sh` exit codes.
2. One table per section A to H: id, verdict, evidence (command, raw output tail, exit code), tag.
3. Findings, FAIL and OPEN first. Each finding has a severity, `file:line`, the proposed fix, the risk, the deny and allow tests that prove the fix, and acceptance criteria.
4. The BLOCKED list, with the unblock Aditya must do for each row.

In chat: the answer first, then FAIL and OPEN rows first, at most 5 rows. Then one status line: DONE verified, DONE_WITH_CONCERNS, BLOCKED or NEEDS_CONTEXT. End with one next step: the first fix for Aditya to approve.
