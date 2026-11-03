"""Point in time company facts from SEC EDGAR (`data.sec.gov`), stdlib HTTP only.

A fact counts for `as_of` only when it was filed on or before that date (`filed <= as_of`) and
its period ended by then; a fact filed later is invisible, whatever it restates. For each field
the latest period end wins, then the latest filing, then the shortest period (a quarter before a
year to date total), then the accession number, so the choice is deterministic. Values are
`Decimal`; a boolean, a non finite number, or a negative value in a field that cannot be
negative is dropped, never repaired. Entity names and form codes are SEC text: the desk sees them
only inside a fence.
"""

import json
import re
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from traderbot import cache

EDGAR_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
USER_AGENT = "traderbot/0.1 (local-paper-research)"
CIK_RE = re.compile(r"[0-9]{1,10}")
TIMEOUT_S = 20
MAX_BYTES = 128 * 1024 * 1024
FACTS_TTL_S = 86_400
FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A"})

# field -> (taxonomy, concept) candidates; the latest eligible fact across all of them wins.
CONCEPTS: dict[str, tuple[tuple[str, str], ...]] = {
    "revenues": (
        ("us-gaap", "Revenues"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "SalesRevenueNet"),
    ),
    "net_income": (("us-gaap", "NetIncomeLoss"),),
    "assets": (("us-gaap", "Assets"),),
    "liabilities": (("us-gaap", "Liabilities"),),
    "operating_cash_flow": (("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),),
    "shares_outstanding": (
        ("us-gaap", "CommonStockSharesOutstanding"),
        ("dei", "EntityCommonStockSharesOutstanding"),
    ),
}
UNITS = {"shares_outstanding": "shares"}  # every other field is USD
NON_NEGATIVE = frozenset({"revenues", "assets", "liabilities", "shares_outstanding"})


@dataclass(frozen=True)
class Fact:
    field: str
    concept: str
    value: Decimal
    start: date | None  # None for an instant (a balance or a share count)
    end: date
    filed: date
    form: str
    fiscal_period: str
    accession: str

    @property
    def days(self) -> int:
        return 0 if self.start is None else (self.end - self.start).days


@dataclass(frozen=True)
class FundamentalSnapshot:
    symbol: str
    cik: str  # ten digits
    entity_name: str
    as_of: date
    facts: tuple[Fact, ...]  # in CONCEPTS order, only the fields found
    missing: tuple[str, ...]


def cik10(cik: str) -> str:
    if not CIK_RE.fullmatch(cik):
        raise ValueError(f"a CIK is 1 to 10 digits, got {cik!r}")
    return cik.zfill(10)


def _http_get_json(url: str, headers: dict[str, str]) -> Any:
    """GET `url` and parse JSON. The one network call in this module; offline tests trip it."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError(f"{url} returned more than {MAX_BYTES} bytes")
    return json.loads(body)


def company_facts_url(cik: str) -> str:
    return EDGAR_URL.format(cik=cik10(cik))


def fetch_company_facts(cik: str, now: datetime) -> Any:
    """The companyfacts JSON for `cik`, cached for a day, transient failures retried."""
    url = company_facts_url(cik)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    return cache.get_or_fetch(
        "edgar_facts", cik10(cik), now, FACTS_TTL_S, lambda: _http_get_json(url, headers)
    )


def check_ticker(symbol: str, cik: str, now: datetime) -> None:
    """ValueError unless EDGAR lists `symbol` among the tickers of `cik` (BRK.B reads BRK-B)."""
    padded = cik10(cik)
    url = SUBMISSIONS_URL.format(cik=padded)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    payload = cache.get_or_fetch(
        "edgar_submissions", padded, now, FACTS_TTL_S, lambda: _http_get_json(url, headers)
    )
    raw = payload.get("tickers") if isinstance(payload, Mapping) else None
    tickers = [t.upper() for t in raw if isinstance(t, str)] if isinstance(raw, list) else []
    if symbol.upper().replace(".", "-") not in tickers:
        listed = ", ".join(tickers[:5]) or "none"
        raise ValueError(f"CIK {cik} is not {symbol} on EDGAR (its tickers: {listed})")


def _day(raw: object) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _value(raw: object) -> Decimal | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _candidates(
    field: str, taxonomy: str, concept: str, facts: Mapping[str, Any], as_of: date
) -> list[Fact]:
    section = facts.get(taxonomy)
    if not isinstance(section, Mapping):
        return []
    entry = section.get(concept)
    units = entry.get("units") if isinstance(entry, Mapping) else None
    rows = units.get(UNITS.get(field, "USD")) if isinstance(units, Mapping) else None
    if not isinstance(rows, list):
        return []
    found: list[Fact] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        filed, end, start = _day(row.get("filed")), _day(row.get("end")), _day(row.get("start"))
        value, form = _value(row.get("val")), row.get("form")
        if filed is None or end is None or value is None or not isinstance(form, str):
            continue
        if filed > as_of or end > as_of or form not in FORMS:
            continue  # the point in time rule: nothing filed after as_of, nothing ending after it
        if "start" in row and (start is None or start > end):
            continue
        if field in NON_NEGATIVE and value < 0:
            continue
        found.append(
            Fact(
                field=field,
                concept=f"{taxonomy}:{concept}",
                value=value,
                start=start,
                end=end,
                filed=filed,
                form=form,
                fiscal_period=str(row.get("fp", "")),
                accession=str(row.get("accn", "")),
            )
        )
    return found


def _rank(fact: Fact) -> tuple[date, date, int, str]:
    return (fact.end, fact.filed, -fact.days, fact.accession)


def point_in_time_facts(
    symbol: str, cik: str, as_of: date, company_facts: Mapping[str, Any]
) -> FundamentalSnapshot:
    """The latest fact per field that was public by `as_of`, from a companyfacts payload."""
    padded = cik10(cik)
    raw_cik = company_facts.get("cik")
    if (
        isinstance(raw_cik, bool)
        or not isinstance(raw_cik, (int, str))
        or str(raw_cik).zfill(10) != padded
    ):
        raise ValueError(f"companyfacts is for CIK {raw_cik!r}, want {padded}")
    facts = company_facts.get("facts")
    if not isinstance(facts, Mapping):
        raise TypeError("companyfacts has no facts object")
    chosen: list[Fact] = []
    missing: list[str] = []
    for field, concepts in CONCEPTS.items():
        pool = [
            fact
            for taxonomy, concept in concepts
            for fact in _candidates(field, taxonomy, concept, facts, as_of)
        ]
        if pool:
            chosen.append(max(pool, key=_rank))
        else:
            missing.append(field)
    name = company_facts.get("entityName")
    return FundamentalSnapshot(
        symbol=symbol,
        cik=padded,
        entity_name=name if isinstance(name, str) else "",
        as_of=as_of,
        facts=tuple(chosen),
        missing=tuple(missing),
    )


def load_snapshot(symbol: str, cik: str, as_of: date, now: datetime) -> FundamentalSnapshot:
    payload = fetch_company_facts(cik, now)
    if not isinstance(payload, Mapping):
        raise TypeError("companyfacts is not a JSON object")
    return point_in_time_facts(symbol, cik, as_of, payload)
