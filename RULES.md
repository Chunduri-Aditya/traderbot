# RULES

_Conventions for code in this repo. Imported by `CLAUDE.md`, so it loads every session: keep it short. Global rules in `~/.claude/CLAUDE.md` and `~/.claude/rules/` already apply; do not restate them here._

## Approved libraries
- `~/.claude/rules/python.md` and `~/.claude/rules/security.md`. Runtime: `alpaca-py==0.44.0` only (pydantic comes with it). Stdlib for sqlite, TOML and HTTP to `data.sec.gov`.

## Do
- Money is `Decimal` end to end; convert to float only at the `alpaca-py` request boundary.
- Every "now" is `datetime.now(ZoneInfo("America/New_York"))`, taken once per command and passed down.
- A new rule is a named reason code in `gate.py` with a deny and an allow test whose input crosses the limit.
- Every trading client goes through `broker.assert_paper`; the check raises, never a bare `assert`.
- Tests set `TRADERBOT_HOME` to `tmp_path`; nothing but the real CLI touches `~/.traderbot`.
- A borrowed idea gets a Provenance row in `ARCHITECTURE.md` in the same commit.

## Do not
- Never paste code from `repos/`; never read or print the Keychain values; never run `approve` from an agent shell (deny rules in `.claude/settings.json`).
- Never let the model set qty, price, order type or time in force.
- No `skip` in `tests/test_smoke.py`: a REVIEW fails the desk half with the desk's reason, and the order half never depends on the desk choosing buy.

## Errors
- A deliberate stop (spend cap, stale bars, tamper, gate deny, bad prefix) is `cli.Refused`: one line on stderr, exit 1. Anything else propagates with its traceback.
- Any desk failure (timeout, bad JSON, error envelope, stop reason, model mismatch, schema) is a REVIEW card, charged at the call cap when the cost is missing.

## Constraints
- Paper only. Limits (Phase 1, 2026-09-29): $1,000 max notional, 0.5% limit band, $2.00 day model spend, $0.25 per desk call.
- The free Alpaca data plan serves SIP history once it is 15 or more minutes old, so the newest bar counts only 16 minutes after the close.
