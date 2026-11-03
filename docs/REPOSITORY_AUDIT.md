# Repository audit

_Read on `3e8ef71` (main), fixes on `claude/gifted-maxwell-5ha5tq`. Linux, Python 3.13.18, uv 0.11.32, no Alpaca keys, no `claude` CLI, no `/dev/tty`. `PHASES.md` is the only roadmap; this file records evidence, not plans._

Three sections. **Confirmed**: a command or test was run here and its output is quoted. **Inferred**: read in code, not run. **Unimplemented**: stubs and unbuilt phases.

## Confirmed

### C1. The offline gates pass
- `./scripts/check.sh` on `3e8ef71`: `ruff=0`, `fmt=0`, `mypy=0`, `pytest=0`, `137 passed, 2 deselected`.
- The same after the fix in C2: `144 passed, 2 deselected`, four zeros.
- `uv run python scripts/mutate.py --scratch "$(mktemp -d)"`: `rows=27 red=27 not_red=0`, `gate_restored=clean`, before and after the fix.

### C2. The daily order cap leaked by one per claim taken over on a later day (fixed)
- Where: the takeover branch of `claim_order` (`src/traderbot/ledger.py:250-298` after the fix). Before the fix it updated `status` and `updated_at` only and returned before the cap count, so the claim kept the day of the failed submit.
- Path: propose after Monday's close (the card expires at Tuesday's close), approve, the submit raises. On Tuesday the retry is placed but counted on Monday, and three more cards pass `DAILY_ORDER_CAP`: four Tuesday orders against a cap of 3.
- Red on `3e8ef71` (new tests in `tests/test_ledger.py` and `tests/test_approve.py`): `6 failed, 1 passed`, with `DID NOT RAISE ClaimRefused` for both takeover paths, `'2026-11-02' != '2026-11-03'` for the moved day, `['claimed', 'claimed']` for two racing takeovers, and `DID NOT RAISE Refused` for the fourth Tuesday approve. The same day takeover test passed, as intended.
- Fix: a later day takeover counts the new day's orders inside the same `BEGIN IMMEDIATE` block (`ledger.py:261` opens it, `277-283` count and refuse, `284-288` update), refuses with `DAILY_ORDER_CAP` leaving the row unchanged, else moves `submitted_day` to the new day. Green: `7 passed`. With the refusal deleted in a temp copy of the repo, the full day tests and the race test failed again (`3 failed`).
- The race test passed 30 of 30 repeated runs. Atomicity rests on the single transaction, not on the test.

### C3. The paper host check and the desk failure paths hold
`tests/test_broker.py` and `tests/test_desk.py`: `13 passed`, including `test_live_client_is_refused`, `test_overridden_host_is_refused`, `test_kill_switch_refuses_a_submit_even_called_directly`, `test_timeout_kills_the_whole_process_group`, `test_short_or_qty_fields_are_refused_by_the_schema`.

### C4. No credential pattern in tracked files
`git grep -nIE` over AWS, GitHub, `sk-`, Alpaca style `PK` and PEM private key patterns: no hit, `d1=1`, after the positive control printed `1`.

### C5. No CI
`ls .github`: `No such file or directory`. The suite in C1 already runs offline on Linux without secrets.

## Inferred

- **Buying power may include margin.** The gate compares against `TradeAccount.buying_power` (`broker.py:45-48`, `gate.py:108`). Alpaca reports margin buying power on margin accounts, and paper accounts default to margin. Not checked against this account's `multiplier`.
- **No aggregate exposure cap.** `Limits` (`gate.py:32-42`) caps notional per order and position per symbol at 10%, not total gross exposure.
- **The loss streak half of the breaker is inert.** Only `ledger.py:510` reads `settlements`; nothing writes it until Phase 4. Drawdown and the latch work.
- **No live quote check at approve.** Limits come from the prior close (`proposal.py:63-85`); a card approved after a gap is not compared to the current price. A buy limit above the close still caps the fill price.
- **Raw prices reach the desk.** `Adjustment.RAW` (`data.py:114`) is right for sizing and wrong as history across a split in the 45 day window.
- **Ledger file modes.** `home()` creates the directory at 0700 (`ledger.py:122`) but does not tighten an existing one, and the sqlite file takes the umask.
- **The spec is not in the clone.** `PHASES.md` names plans under `~/.claude/plans/` and `CLAUDE.md` names `MEMORY.md`; neither is tracked.
- **Sound by reading:** money stays `Decimal` until `broker.py:116`; every `now` is ET aware (`data.py:29-35`, `72-75`); a session's bar counts only 16 minutes after its close (`data.py:25`, `93-100`); the model chooses only symbol, side, rationale and confidence (`proposal.py:22-30`); every SQL call binds parameters; model text is escaped before the terminal (`cli.py:59-65`).

## Unimplemented

- Six stub modules raise `NotImplementedError`: `backtest.py`, `cache.py`, `fence.py`, `fundamentals.py`, `scoring.py`, `live_gate.py`.
- Phases 3 to 7 are open in [`PHASES.md`](../PHASES.md). Until Phase 6 lands there is no backtest, no cost or slippage model and no strategy result, so no performance claim is supportable.
- The live smoke (`uv run pytest -m live tests/test_smoke.py`) is BLOCKED here: it needs the Keychain keys on Aditya's Mac.
