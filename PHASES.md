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
- [ ] Calendar staleness, adjusted history, cache with retry, EDGAR point in time facts; a script proves `filed <= as_of` on a live fetch.

## Phase 4: scoring and memory
- [ ] Settlement after 5 sessions, alpha against SPY, Brier, bootstrap CI; report equals hand computed values.

## Phase 5: desk
- [ ] Nonce fence, seven role full desk, REVIEW on any role failure or budget stop; a live full desk card.

## Phase 6: baseline and live check
- [ ] Rules baseline backtest equals hand values; `live-check` prints FAIL with reasons on a young ledger.

## Phase 7: Fable audit
- [ ] Four fresh Fable reviewers; findings in `audits/traderbot-fable-audit.md`; fixes land with failing tests first.
