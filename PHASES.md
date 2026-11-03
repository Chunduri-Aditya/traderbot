# PHASES

_Sequential milestones, one at a time. Committed. No dates: a phase is done when its check passes. Tick a box only when its evidence exists. Spec (what to build): `~/.claude/plans/let-s-merge-all-of-transient-quasar.md`. Runbook (how to run it, and its deltas win over the spec): `~/.claude/plans/fine-prep-the-plan-humble-duckling.md`._

## Phase 0: setup
- [x] Repo, ignore rules, Opus project settings with deny rules, audits, `~/.traderbot/` at 0700 (`2e5442e`).

## Phase 1: thin path
- [x] `uv run pytest -m live tests/test_smoke.py` prints `2 passed` on a tmp ledger (desk half and order half), with no skip in the file.
- [x] `uv run traderbot propose SPY` and `status` on the real ledger (card `f5cd6072`, a hold, so nothing to approve; the positive approve control moves to Phase 2).
- [x] Six docs pass `check_project_docs.py`; `repos/` is untracked.
- [x] Fresh Fable review: signed `requested_symbol`, stale bars refused before the desk, approve refusal paths tested offline with mutation rows red.
- [x] `Bash(/usr/bin/security find-generic-password:*)` in the project deny list (added by Aditya; the classifier refuses the agent's settings edit).

## Phase 2: gate and approval
- [x] Fourteen reason codes, each with a deny and an allow test; `/dev/tty` approval; reject; card expiry; first decision wins; kill switch; spend reservations; append only, redacted events; sell side; breaker latch; `--json` on every command (`47f3fef`, `90b7135`).
- [x] `scripts/check.sh` all zeros; `scripts/mutate.py` 26 of 26 rows red (`0a935fe`).
- [x] Negative controls: the deny rule stops the agent's `uv run traderbot approve`; the offline TTY test fails closed with Errno 6 and no decision.
- [x] Attack workflow (`98b2fa3` harness): 21 cases, 16 confirmed by Fable verifiers, all fixed in `663c9e4` with their tests; `mutate.py` 27 of 27 red.
- [x] Positive control: Aditya ran `uv run traderbot approve` in Terminal.app, typed `5894fe66`, and paper order `f6c889d0` came back `accepted` (read back from Alpaca: SPY buy 1 limit 768.02 day).

## Phase 3: data
- [x] `certifi` bumped to 2026.10.23 (PYSEC-2026-4320 closed; `pip-audit` reports none) (`e4afd14`).
- [x] Calendar staleness (the as_of bar counts 16 minutes after the close, a partial window is never cached), RAW and adjusted history, `cache.py` with retry on transient errors only, EDGAR point in time facts; tests in `test_cache.py`, `test_data.py`, `test_fundamentals.py`.
- [x] `scripts/pit_check.py` on a live fetch (cloud session, 2026-11-03): AAPL CIK 320193 at `as_of` 2026-11-02 chose the Q3 10-Q filed 2026-07-31 and none of the 463 facts the FY2026 10-K filed 2026-11-03 carries; `chosen=6 leaked=0`, exit 0.

## Phase 4: scoring and memory
- [x] `traderbot settle` and `traderbot score` (`--json` on both): settlement 5 exchange sessions after `as_of` on adjusted closes, alpha against SPY over the same closes, Brier, a seeded paired bootstrap CI; `tests/test_scoring.py` checks hand computed values (buy +3% against SPY +1% is 200 bps, Brier 0.09), a holiday inside the horizon, 4 sessions not settling, and 3 settled losing fills tripping `CIRCUIT_BREAKER`.
- [ ] A live `settle` on the real ledger once a card is 5 sessions old (Aditya's Mac: needs the Keychain).

## Phase 5: desk
- [x] Nonce fence (`fence.py`), seven role desk in one call (`desk_label` `full`), EDGAR facts by `--cik` and the settled record fenced in the prompt, REVIEW on a fence leak, a budget stop (any stop reason but `end_turn` or `tool_use`) or any other desk failure; tests in `test_fence.py`, `test_desk.py` (forged closes in an EDGAR name and a form code, a nonce echoed back through `decide`) and `test_approve.py`.
- [ ] A live full desk card (Aditya's Mac: needs the Keychain and the claude CLI).

## Phase 6: baseline and live check
- [x] `traderbot backtest SYMBOL` (`sma`, `mean_rev`) through `proposal.size`, `gate.check` and `scoring.settle`; `tests/test_backtest.py` equals hand values (the SMA cross buys 9 at 110.55 on bar 30 for 900 bps; mean reversion buys 12 at 80.40 and sells 10 then 2 at 99.50), no decision moves when later bars change, and the breaker stops a baseline after 3 settled losses.
- [x] `traderbot live-check` prints `FAIL: INSUFFICIENT_SETTLEMENTS, NEGATIVE_ALPHA_CI, BRIER_OVER_MAX, NO_BASELINE` on a young ledger and exits 1 (`test_live_check_fails_on_a_young_ledger_and_exits_one`); each of the eight codes has a deny and an allow on its limit, with mutation rows.
- [ ] A live `backtest SPY` and `live-check` on the real ledger (Aditya's Mac: needs the Keychain).

## Phase 7: Fable audit
- [ ] Four fresh Fable reviewers; findings in `audits/traderbot-fable-audit.md`; fixes land with failing tests first.
