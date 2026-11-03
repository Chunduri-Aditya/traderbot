# traderbot Phase 7 audit (cloud session, 2026-11-03)

Scope: the Phase 3 to 6 code (`cache.py`, `data.py`, `fundamentals.py`, `scoring.py`, `fence.py`, `desk.py`, `backtest.py`, `live_gate.py`) and how it plugs into `cli.py`, `gate.py` and `ledger.py`. Commits `e4afd14..HEAD` on `claude/friendly-babbage-2bnlca`.

What this is: one attack pass by the building session, written as `tests/test_attack_p3_p6.py` (18 vectors), plus one independent reviewer agent briefed only with the code. It is not the four fresh Fable reviewers `PHASES.md` names for Phase 7. That audit is still open.

Tags: VERIFIED means run in this container with the exit code shown. INFERRED means read in code.

## Findings fixed (each red before its fix, green after)

| Id | Severity | Where | Defect | Fix | Test |
|---|---|---|---|---|---|
| A1 | risk | `cache.py` `lookup` | Python's JSON reader accepts bare `NaN` and `Infinity`, so a cache row holding them under a valid digest was served as data | `parse_constant` raises, so the row is a miss and gets refetched | `test_v09_a_non_finite_body_under_a_valid_digest_is_a_miss` (3 cases red, then green) |
| A2 | risk | `fence.py` `verify_no_fence_leak` | a zero width space, word joiner, BOM, soft hyphen or combining grapheme joiner inside a fence tag or the nonce hid an echo from the leak check | check `text.visible_only` (NFKC folded, Cf and invisible characters removed) | `test_v05_a_fence_tag_split_by_an_invisible_character_is_still_a_leak` (5 cases red, then green) |

## Vectors that held on the first run (VERIFIED, `tests/test_attack_p3_p6.py`)

EDGAR filing lookahead and period lookahead, fence escape through an EDGAR name and a form code, the desk setting qty, price, order type or time in force, non finite EDGAR values, non finite or non positive prices at settlement (nothing stored), an edited cache body, 20 concurrent spend reservations (8 won, 12 refused, $2.00 spent), two concurrent settlers (one row), horizon off by one (4 sessions, one second early, a half day, a weekend as_of), live-check boundary flips, code built cards padding the desk record, a negative unconfirmed count, a hostile desk symbol at settle, future SPY closes in a backtest, and CIK path injection.

## Known limits (not defects; written down so nobody reads them as claims)

- Hold cards are not scored, so a desk that always holds builds no record and never passes live-check. Scoring holds needs a counterfactual definition Aditya has not chosen.
- Baseline alpha comes from one symbol's backtest, while desk alpha spans whatever symbols were proposed. The live-check comparison is directional, not like for like.
- Backtests fill at the limit with no partial fills, no commissions (Alpaca charges none) and no slippage beyond the 0.5% band. Half days are treated as full sessions inside a backtest; live settlement uses the real calendar.
- `filed <= as_of` lets a filing made after the close on `as_of` count. A live propose runs after that close, so the filing is public by then. A backtest deciding at that close would see it slightly early.
- Adjusted closes are fetched as of the settlement day, so a later split or dividend shifts P0 and P5 together. The return holds, but stored prices are not raw.
