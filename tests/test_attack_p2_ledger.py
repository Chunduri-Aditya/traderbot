"""Phase 2 attack lens: ledger races, double approve, tamper, and the CLI and TTY paths.

Offline only. The copied conftest makes claude, the Keychain and every HTTP request tripwires and
points TRADERBOT_HOME at tmp_path. Nothing here opens /dev/tty: the terminal is a fake object and
no subprocess is started. Alpaca is a dict of orders keyed by client id.
"""

import contextlib
import io
import sqlite3
import threading
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Self

import pytest
import requests
from support import NOW, SESSIONS, a_card, trip

from traderbot import broker, cli, data, gate, keychain, ledger
from traderbot import proposal as prop


class FakeAlpaca:
    """The paper server as far as approve can see it: orders by client id."""

    def __init__(self) -> None:
        self.placed: dict[str, Any] = {}
        self.submits: list[str] = []

    def lookup(self, trading: object, client_id: str) -> Any:
        return self.placed.get(client_id)

    def place(self, card: prop.Proposal, client_id: str) -> Any:
        order = SimpleNamespace(
            id=uuid.uuid4(),
            status=SimpleNamespace(value="accepted"),
            client_order_id=client_id,
            symbol=card.symbol,
        )
        self.placed[client_id] = order
        return order

    def open_symbols(self, trading: object) -> frozenset[str]:
        return frozenset(order.symbol for order in self.placed.values())


class FakeTerminal:
    """Stands in for /dev/tty: approve writes its list here and reads what Aditya typed."""

    def __init__(self, typed: str) -> None:
        self.typed = io.StringIO(typed)
        self.out = io.StringIO()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def write(self, text: str) -> int:
        return self.out.write(text)

    def flush(self) -> None:
        return None

    def readline(self) -> str:
        return self.typed.readline()


@pytest.fixture
def alpaca(monkeypatch: pytest.MonkeyPatch) -> FakeAlpaca:
    """$100k buying power and equity, no position, the clock at 18:00 ET on 2026-09-29."""
    fake = FakeAlpaca()
    monkeypatch.setattr(keychain, "load", lambda: keychain.AlpacaKeys("key", "secret"))
    monkeypatch.setattr(broker, "client", lambda keys: object())
    monkeypatch.setattr(broker, "order_by_client_id", fake.lookup)
    monkeypatch.setattr(broker, "account", lambda trading: object())
    monkeypatch.setattr(broker, "buying_power", lambda acct: Decimal(100000))
    monkeypatch.setattr(broker, "equity", lambda acct: Decimal(100000))
    monkeypatch.setattr(broker, "position", lambda trading, symbol: (0, Decimal(0)))
    monkeypatch.setattr(broker, "open_order_symbols", fake.open_symbols)
    monkeypatch.setattr(broker, "submit_limit_order", trip)  # each test installs its own
    monkeypatch.setattr(data, "now_et", lambda: NOW)
    monkeypatch.setattr(data, "sessions", lambda trading, start, end: SESSIONS)
    return fake


def store(conn: sqlite3.Connection, card: prop.Proposal) -> str:
    return ledger.insert_card(conn, card, Decimal("0.01"), ("claude-sonnet-5-5",), gate.Allow())


def race(
    alpaca: FakeAlpaca, monkeypatch: pytest.MonkeyPatch, first: str, second: str
) -> tuple[Any, list[Any]]:
    """Approve `first`; while its order is on the wire, a second approve of `second` runs.

    The first submit waits up to 2 s for the second approve to finish, so an approve serialized
    by a lock still completes (the second one then runs after the first order is recorded).
    """
    in_flight = threading.Event()
    second_done = threading.Event()
    outcome: list[Any] = []

    def second_approver() -> None:
        in_flight.wait(timeout=10)
        try:
            outcome.append(cli.approve(second))
        except BaseException as err:  # noqa: BLE001 (reported by race(), never swallowed)
            outcome.append(err)
        finally:
            second_done.set()

    def submit(trading: object, card: prop.Proposal, client_order_id: str) -> Any:
        alpaca.submits.append(client_order_id)
        if not in_flight.is_set():
            in_flight.set()
            second_done.wait(timeout=2)
        return alpaca.place(card, client_order_id)

    monkeypatch.setattr(broker, "submit_limit_order", submit)
    thread = threading.Thread(target=second_approver)
    thread.start()
    result = cli.approve(first)
    thread.join(timeout=30)
    assert not thread.is_alive()
    unexpected = [o for o in outcome if isinstance(o, BaseException)]
    unexpected = [o for o in unexpected if not isinstance(o, cli.Refused)]
    assert unexpected == [], unexpected
    return result, outcome


# Double approve


def test_two_approves_in_flight_submit_the_card_once(
    alpaca: FakeAlpaca, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runbook, Phase 2 notes: "**Approve order:** recompute the digest;
    `get_order_by_client_id(digest)`; if an order exists, return it and submit nothing; otherwise
    rerun the gate live and submit the stored fields." Runbook Deltas: "the decision is recorded
    after the live gate passes, then the order is submitted; a second approve finds the order by
    client id". Spec: "The first decision wins through a sqlite UNIQUE constraint inside one
    transaction."

    Expected: one submit for the card's digest. `_decide` takes a standing approve written by the
    other approver as its own, so a second approve that looks up the client id while the first
    order is still on the wire submits the same card again; only Alpaca's server could stop it.
    """
    conn = ledger.connect()
    key = store(conn, a_card())
    race(alpaca, monkeypatch, key[:8], key[:8])
    assert alpaca.submits == [key]


def test_two_approves_in_flight_stay_within_the_daily_order_cap(
    alpaca: FakeAlpaca, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec, Phase 2 gate rules: "`DAILY_ORDER_CAP`, `DAY_SPEND_CAP`, `KILL_SWITCH`,
    `CARD_EXPIRED`;". Runbook, Numbers asked at each phase start: "| 2 | daily order cap | 3 |".

    Expected: at most 3 orders on the day. Two orders are already recorded; two cards on different
    symbols are approved together. Each live gate reads `orders_today` as 2 before either order is
    recorded, so both submit and the day ends with 4; Alpaca has no copy of this cap.
    """
    conn = ledger.connect()
    for tag in ("earlier one", "earlier two"):
        earlier = store(conn, a_card(rationale=tag))
        ledger.record_order(conn, earlier, f"order {tag}", "filled", NOW, "1", "767.10")
    spy = store(conn, a_card(rationale="first"))
    qqq = store(conn, a_card(symbol="QQQ", requested_symbol="QQQ", rationale="second"))
    assert ledger.orders_today(conn, NOW.date()) == 2
    race(alpaca, monkeypatch, spy[:8], qqq[:8])
    assert ledger.orders_today(conn, NOW.date()) <= 3


# An approve that fails after its decision


def test_approve_retried_at_the_terminal_after_a_dropped_response_finds_the_order(
    alpaca: FakeAlpaca, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec, Phase 2 approval: "`approve` reads the 8 char prefix from `/dev/tty` and fails closed
    without a TTY." Runbook Deltas: "the decision is recorded after the live gate passes, then the
    order is submitted; a second approve finds the order by client id".

    Expected: the second `traderbot approve` returns the order Alpaca holds for the digest. The
    submit reached Alpaca but the response was lost, so the ledger has the approve decision and no
    order row; the terminal lists only undecided cards, so the retry refuses with "no card is
    waiting" and the placed order never reaches the ledger (status and `orders_today` miss it).
    """
    conn = ledger.connect()
    key = store(conn, a_card())

    def accepted_then_dropped(trading: object, card: prop.Proposal, client_order_id: str) -> Any:
        alpaca.submits.append(client_order_id)
        alpaca.place(card, client_order_id)
        raise requests.exceptions.ConnectionError("Connection aborted: RemoteDisconnected")

    monkeypatch.setattr(broker, "submit_limit_order", accepted_then_dropped)
    monkeypatch.setattr(cli, "open_tty", lambda: FakeTerminal(key[:8] + "\n"))
    with pytest.raises(requests.exceptions.ConnectionError):
        cli.approve_at_terminal()
    assert list(alpaca.placed) == [key]
    monkeypatch.setattr(broker, "submit_limit_order", trip)
    assert cli.approve_at_terminal() is alpaca.placed[key]


def test_a_submit_that_never_reached_alpaca_leaves_the_card_open_to_a_reject(
    alpaca: FakeAlpaca, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runbook Deltas: "| approve records its decision first | a gate deny would leave an approve
    that later blocks a reject | the decision is recorded after the live gate passes, then the
    order is submitted; a second approve finds the order by client id |".

    Expected: `reject` records "reject". The submit fails before Alpaca sees it, so no order
    exists, yet the approve decision stands: reject is refused "already decided: approve" and the
    terminal no longer lists the card, the state the Deltas row set out to remove.
    """
    conn = ledger.connect()
    key = store(conn, a_card())

    def unreachable(trading: object, card: prop.Proposal, client_order_id: str) -> Any:
        raise requests.exceptions.ConnectionError("Failed to establish a new connection")

    monkeypatch.setattr(broker, "submit_limit_order", unreachable)
    with pytest.raises(requests.exceptions.ConnectionError):
        cli.approve(key[:8])
    assert alpaca.placed == {}
    assert cli.reject(key[:8]) == "reject"


# Tamper and the audit log


def test_a_tampered_card_with_non_ascii_text_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """RULES.md, Errors: "A deliberate stop (kill switch, spend cap, stale bars, tripped breaker,
    tamper, gate deny, bad prefix, no terminal) is `cli.Refused`: one line on stderr, exit 1.
    Anything else propagates with its traceback."
    Spec: "A tampered card (digest mismatch) is refused."

    Expected: `cli.Refused`. An edit that puts one non ASCII character in the stored text makes
    `digest_of` raise UnicodeEncodeError, so the tamper surfaces as a traceback, not a refusal.
    """
    monkeypatch.setattr(keychain, "load", trip)
    monkeypatch.setattr(broker, "submit_limit_order", trip)
    conn = ledger.connect()
    key = store(conn, a_card())
    with conn:
        conn.execute(
            "UPDATE cards SET proposal_json = replace(proposal_json, 'offline test card', ?)",
            ("offline test café",),
        )
    assert "café" in conn.execute("SELECT proposal_json FROM cards").fetchone()[0]
    with pytest.raises(cli.Refused):
        cli.approve(key[:8])


def test_insert_or_replace_cannot_rewrite_an_event() -> None:
    """Spec, Phase 2: "**Audit log**: an `events` table made append only by sqlite triggers that
    raise on UPDATE and DELETE, with key redaction (paperclip
    `server/src/services/activity-log.ts:161-173`)."

    Expected: the logged event is unchanged. REPLACE resolves the primary key conflict by deleting
    the old row, and sqlite fires delete triggers for that only when recursive_triggers is on, so
    a reject's decision event is rewritten as an approve with no trigger raising.
    """
    conn = ledger.connect()
    key = store(conn, a_card())
    ledger.log_event(conn, "decision", NOW, key, {"decision": "reject"})
    before = conn.execute("SELECT seq, kind, digest, details_json FROM events").fetchall()
    ((seq, _, _, _),) = before
    with contextlib.suppress(sqlite3.DatabaseError), conn:
        conn.execute(
            "INSERT OR REPLACE INTO events (seq, at, kind, digest, details_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (seq, NOW.isoformat(), "decision", key, '{"decision": "approve"}'),
        )
    after = conn.execute("SELECT seq, kind, digest, details_json FROM events").fetchall()
    assert after == [(seq, "decision", key, '{"decision": "reject"}')]


def test_event_details_under_plural_credential_keys_are_redacted() -> None:
    """ARCHITECTURE.md, Provenance: "| Redact logged details by key name at any depth | paperclip
    `server/src/redaction.ts:881`, `server/src/services/activity-log.ts:75-87` | MIT | 2 |".
    Spec: "with key redaction".

    Expected: no value under a key naming a credential reaches `details_json`. The rebuilt matcher
    compares whole words against a singular list, so `apiKeys`, `secrets`, `tokens` and
    `passwords` pass through, while the cited paperclip pattern matches them as substrings.
    """
    conn = ledger.connect()
    ledger.log_event(conn, "control", NOW, details={"api_key": "PKSINGULAR01"})
    (control,) = conn.execute("SELECT details_json FROM events").fetchone()
    assert "PKSINGULAR01" not in control  # the singular key is redacted, so redaction ran
    plural = {
        "apiKeys": ["PKPLURAL0001"],
        "secrets": {"alpaca": "SKPLURAL0002"},
        "tokens": ["TKPLURAL0003"],
        "passwords": "PWPLURAL0004",
    }
    ledger.log_event(conn, "debug", NOW, details=plural)
    (raw,) = conn.execute("SELECT details_json FROM events WHERE kind = 'debug'").fetchone()
    leaked = [
        v for v in ("PKPLURAL0001", "SKPLURAL0002", "TKPLURAL0003", "PWPLURAL0004") if v in raw
    ]
    assert leaked == []


# Terminal escaping


def test_shown_escapes_marks_that_render_as_nothing() -> None:
    """ARCHITECTURE.md, Provenance: "| Escape every character that can drive a terminal or render
    as nothing, Hangul fillers included | `/Users/chunduri/Desktop/twin/src/twin/render.py:17-28`
    | Aditya's own | 2 |". Spec: "model text escaped for the terminal".

    Expected: none of these reaches the terminal raw. Each is a Unicode Default_Ignorable_Code_Point
    of category Mn (combining grapheme joiner, Khmer inherent vowels, a Mongolian free variation
    selector, variation selectors 16 and 17), so `isprintable()` passes it and it renders as
    nothing; FILLERS lists only the default ignorable letters (Lo).
    """
    invisible = ["\u034f", "\u17b4", "\u17b5", "\u180b", "\ufe0f", "\U000e0100"]
    leaked = [f"U+{ord(ch):04X}" for ch in invisible if ch in cli.shown(f"SPY rallied{ch} 2.8%")]
    assert leaked == []
