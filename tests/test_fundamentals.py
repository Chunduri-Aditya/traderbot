"""EDGAR point in time facts offline: nothing filed after as_of, deterministic choice, Decimal
values, bad values dropped, the fetch cached and tripwired."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from support import NOW, Tripwire

from traderbot import fundamentals

AS_OF = date(2026, 9, 29)


def row(
    val: Any, end: str, filed: str, start: str | None = None, form: str = "10-Q", **extra: Any
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "val": val,
        "end": end,
        "filed": filed,
        "form": form,
        "fp": "Q2",
        "accn": f"a-{filed}",
    }
    if start is not None:
        out["start"] = start
    return out | extra


def payload(**concepts: list[dict[str, Any]]) -> dict[str, Any]:
    gaap: dict[str, Any] = {}
    for concept, rows in concepts.items():
        unit = "shares" if concept == "CommonStockSharesOutstanding" else "USD"
        gaap[concept] = {"units": {unit: rows}}
    return {"cik": 320193, "entityName": "Example Corp", "facts": {"us-gaap": gaap}}


def facts(snapshot: fundamentals.FundamentalSnapshot) -> dict[str, fundamentals.Fact]:
    return {fact.field: fact for fact in snapshot.facts}


def test_a_fact_filed_after_as_of_is_invisible() -> None:
    data = payload(
        NetIncomeLoss=[
            row(100, "2026-06-30", "2026-08-01", start="2026-04-01"),
            row(999, "2026-09-27", "2026-09-30", start="2026-07-01"),  # filed the day after as_of
        ]
    )
    chosen = facts(fundamentals.point_in_time_facts("EX", "320193", AS_OF, data))
    assert chosen["net_income"].value == Decimal(100)
    assert chosen["net_income"].filed <= AS_OF


def test_a_fact_filed_on_as_of_counts() -> None:
    data = payload(Assets=[row(5, "2026-06-30", "2026-08-01"), row(7, "2026-06-30", "2026-09-29")])
    assert facts(fundamentals.point_in_time_facts("EX", "320193", AS_OF, data))["assets"].value == 7


def test_a_restatement_filed_later_does_not_rewrite_the_past() -> None:
    original = row(100, "2026-03-31", "2026-05-01", start="2026-01-01")
    restated = row(80, "2026-03-31", "2026-10-15", start="2026-01-01", form="10-Q/A")
    data = payload(NetIncomeLoss=[original, restated])
    assert (
        facts(fundamentals.point_in_time_facts("EX", "320193", AS_OF, data))["net_income"].value
        == 100
    )
    later = fundamentals.point_in_time_facts("EX", "320193", date(2026, 10, 15), data)
    assert facts(later)["net_income"].value == 80


def test_latest_period_wins_across_concepts_then_the_quarter_over_year_to_date() -> None:
    data = payload(
        Revenues=[row(1, "2018-09-29", "2018-11-05", start="2018-07-01", form="10-K")],
        RevenueFromContractWithCustomerExcludingAssessedTax=[
            row(300, "2026-06-30", "2026-08-01", start="2026-01-01"),  # six months
            row(150, "2026-06-30", "2026-08-01", start="2026-04-01"),  # the quarter
        ],
    )
    revenue = facts(fundamentals.point_in_time_facts("EX", "320193", AS_OF, data))["revenues"]
    assert revenue.value == 150 and revenue.days == 90
    assert revenue.concept == "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"


@pytest.mark.parametrize("bad", [True, "NaN", "Infinity", float("inf"), None, "12x", {"v": 1}])
def test_non_numeric_or_non_finite_values_are_dropped(bad: Any) -> None:
    data = payload(
        Assets=[row(10, "2026-03-31", "2026-05-01"), row(bad, "2026-06-30", "2026-08-01")]
    )
    assert (
        facts(fundamentals.point_in_time_facts("EX", "320193", AS_OF, data))["assets"].value == 10
    )


def test_negative_values_only_where_they_can_be_negative() -> None:
    data = payload(
        NetIncomeLoss=[row(-5, "2026-06-30", "2026-08-01", start="2026-04-01")],
        CommonStockSharesOutstanding=[row(-1, "2026-06-30", "2026-08-01")],
    )
    snap = fundamentals.point_in_time_facts("EX", "320193", AS_OF, data)
    assert facts(snap)["net_income"].value == -5
    assert "shares_outstanding" in snap.missing


def test_values_are_decimal_with_no_float_drift() -> None:
    data = payload(Assets=[row(0.1, "2026-06-30", "2026-08-01")])
    value = facts(fundamentals.point_in_time_facts("EX", "320193", AS_OF, data))["assets"].value
    assert isinstance(value, Decimal) and value == Decimal("0.1")


def test_other_forms_and_broken_dates_are_skipped() -> None:
    data = payload(
        Assets=[
            row(1, "2026-06-30", "2026-08-01", form="8-K"),
            row(2, "not a date", "2026-08-01"),
            row(3, "2026-06-30", "2026-08-01", start="2026-07-01"),  # start after end
            row(4, "2026-03-31", "2026-05-01"),
        ]
    )
    assert facts(fundamentals.point_in_time_facts("EX", "320193", AS_OF, data))["assets"].value == 4


def test_a_payload_for_another_company_is_refused() -> None:
    with pytest.raises(ValueError, match="CIK"):
        fundamentals.point_in_time_facts("EX", "789019", AS_OF, payload())


@pytest.mark.parametrize("cik", ["", "12345678901", "32O193", "-1", "1 2"])
def test_a_bad_cik_is_refused(cik: str) -> None:
    with pytest.raises(ValueError, match="CIK"):
        fundamentals.company_facts_url(cik)


def test_the_url_pads_the_cik_to_ten_digits() -> None:
    assert fundamentals.company_facts_url("320193").endswith("/CIK0000320193.json")


def test_offline_tests_cannot_reach_data_sec_gov() -> None:
    with pytest.raises(Tripwire):
        fundamentals.fetch_company_facts("320193", NOW)


def test_the_fetch_sends_the_user_agent_and_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake(url: str, headers: dict[str, str]) -> Any:
        calls.append((url, headers))
        return payload(Assets=[row(9, "2026-06-30", "2026-08-01")])

    monkeypatch.setattr(fundamentals, "_http_get_json", fake)
    first = fundamentals.load_snapshot("EX", "320193", AS_OF, NOW)
    second = fundamentals.load_snapshot("EX", "320193", AS_OF, NOW)
    assert first == second and len(calls) == 1
    assert calls[0][1]["User-Agent"] == "traderbot/0.1 (local-paper-research)"
    assert calls[0][0] == "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
