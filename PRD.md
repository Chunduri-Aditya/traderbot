# PRD

_What is being built and for whom. Committed. Spec: `~/.claude/plans/let-s-merge-all-of-transient-quasar.md`; runbook: `~/.claude/plans/fine-prep-the-plan-humble-duckling.md`._

## Problem
- Aditya wants a personal trading bot built from the six audited repos, and none of them places orders, backtests honestly, or keeps model output away from the order path.

## Users
- Aditya alone, on an Alpaca paper account, approving every order in Terminal.app. Today he has only the audits in `audits/`.

## Goals
- A claude desk proposes US stock and ETF buys on daily bars; a deterministic gate checks each card; Aditya approves; the order lands on paper with the card's digest as its client id.
- Every decision is scored after 5 sessions against SPY, so the desk earns trust only on settled evidence (Phase 4 on).

## Non goals
- No live money: no live code path exists until the Phase 6 live check passes and a separate plan is approved.
- No intraday trading, no options, no crypto, no shorts.
- No autonomous orders: the model never places or sizes an order.

## Features
- `traderbot propose SYMBOL`: bars, one desk call, code sized DAY limit buy, gate verdict, a stored card. Check: the live smoke in `tests/test_smoke.py`.
- `traderbot approve PREFIX`: recompute the digest, return the order already under that client id or rerun the gate live and place it. Check: the smoke's second approve returns the same order id.
- `traderbot status`: cards, orders and the day's model spend from the ledger. Check: run on the real ledger at the Phase 1 exit.
- Later phases: gate hardening and TTY approval (2), data and point in time facts (3), scoring and memory (4), the full desk (5), baseline and live check (6). See `PHASES.md`.
