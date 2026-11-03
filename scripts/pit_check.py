"""Prove the EDGAR point in time rule on a live fetch: every fact chosen for as_of was filed on
or before as_of, and a fact filed after it exists in the payload but was not chosen.

It reads data.sec.gov directly (no cache, no TRADERBOT_HOME) and needs no keys.

Usage: uv run python scripts/pit_check.py --symbol AAPL --cik 320193 --as-of 2026-11-02
"""

import argparse
import sys
from datetime import date

from traderbot import fundamentals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--cik", required=True)
    parser.add_argument("--as-of", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    url = fundamentals.company_facts_url(args.cik)
    payload = fundamentals._http_get_json(url, {"User-Agent": fundamentals.USER_AGENT})
    snap = fundamentals.point_in_time_facts(args.symbol, args.cik, args.as_of, payload)
    later = 0
    for section in payload["facts"].values():
        for entry in section.values():
            for rows in entry.get("units", {}).values():
                later += sum(
                    1 for row in rows if str(row.get("filed", "")) > args.as_of.isoformat()
                )
    print(f"{snap.entity_name} cik {snap.cik} as_of {snap.as_of}")
    for fact in snap.facts:
        print(
            f"  {fact.field:20} {fact.value:>20} end {fact.end} filed {fact.filed}"
            f" {fact.form} {fact.fiscal_period} {fact.concept}"
        )
    print(f"  missing: {', '.join(snap.missing) or 'none'}")
    leaked = [f for f in snap.facts if f.filed > args.as_of or f.end > args.as_of]
    print(f"facts_filed_after_as_of_in_payload={later}")
    print(f"chosen={len(snap.facts)} leaked={len(leaked)}")
    return 1 if leaked else 0


if __name__ == "__main__":
    sys.exit(main())
