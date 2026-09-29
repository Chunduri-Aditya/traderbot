# PHASES

_Sequential milestones, one at a time. Committed. No dates: a phase is done when its check passes. Tick a box only when its evidence exists. Spec (what to build): `~/.claude/plans/let-s-merge-all-of-transient-quasar.md`. Runbook (how to run it, and its deltas win over the spec): `~/.claude/plans/fine-prep-the-plan-humble-duckling.md`._

## Phase 0: setup
- [x] Repo, ignore rules, Opus project settings with deny rules, audits, `~/.traderbot/` at 0700 (`2e5442e`).

## Phase 1: thin path
- [x] `uv run pytest -m live tests/test_smoke.py` prints `2 passed` on a tmp ledger (desk half and order half), with no skip in the file.
- [x] `uv run traderbot propose SPY` and `status` on the real ledger (card `f5cd6072`, a hold, so nothing to approve; the positive approve control moves to Phase 2).
- [x] Six docs pass `check_project_docs.py`; `repos/` is untracked.
- [x] Fresh Fable review: signed `requested_symbol`, stale bars refused before the desk, approve refusal paths tested offline with mutation rows red.
- [ ] `Bash(/usr/bin/security find-generic-password:*)` in the project deny list (Aditya; the classifier refuses the agent's settings edit).

## Phase 2: gate and approval
- [ ] All reason codes with deny and allow tests; `/dev/tty` approval; kill switch; spend reservations; append only audit log; `scripts/check.sh` all zeros; every mutation row red.

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
