"""The CLI surface offline: the TTY barrier, the prefix reader, --json, escaping, the kill switch
commands and reject."""

import io
import json
import os
import select
import subprocess
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from support import a_card

from traderbot import broker, cli, gate, keychain, ledger


def test_approve_without_a_terminal_fails_closed_with_no_decision(tmp_path: Path) -> None:
    conn = ledger.connect()  # TRADERBOT_HOME is tmp_path here and in the child
    ledger.insert_card(conn, a_card(), Decimal("0.01"), (), gate.Allow())  # a card is waiting
    env = {"PATH": os.environ["PATH"], ledger.HOME_ENV: str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, "-m", "traderbot", "approve"],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 1, proc.stderr
    assert "Errno 6" in proc.stderr and "/dev/tty" in proc.stderr
    assert conn.execute("SELECT count(*) FROM decisions").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM orders").fetchone() == (0,)


def test_approve_prompt_reads_a_prefix_typed_at_a_real_terminal(tmp_path: Path) -> None:
    # The child is a session leader whose controlling terminal is a pty slave, as in Terminal.app;
    # it opens /dev/tty the way approve does, prompts, and reads what this test types at the master.
    child = (
        "import fcntl, sys, termios\n"
        "fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
        "from traderbot import cli\n"
        "with cli.open_tty() as tty:\n"
        "    typed = cli.read_prefix(tty, tty)\n"
        "sys.stdout.write(f'\\ntyped={typed}\\n')\n"
    )
    master, slave = os.openpty()
    env = {"PATH": os.environ["PATH"], ledger.HOME_ENV: str(tmp_path)}
    proc = subprocess.Popen(
        [sys.executable, "-c", child],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
        env=env,
    )
    os.close(slave)
    seen = b""
    typed = False
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.2)
        if ready:
            try:
                chunk = os.read(master, 4096)
            except OSError:  # Linux reports the closed slave as EIO, macOS as EOF
                break
            if not chunk:
                break
            seen += chunk
        if not typed and b"prefix" in seen:
            os.write(master, b"3f9a1c2e\n")
            typed = True
        elif not ready and proc.poll() is not None:
            break
    os.close(master)
    out = seen.decode(errors="replace").replace("\r\n", "\n")
    assert proc.wait(timeout=10) == 0, out
    assert "character prefix of the card to approve" in out
    assert "\ntyped=3f9a1c2e\n" in out


def test_open_tty_reports_only_a_missing_terminal_as_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only ENXIO means no controlling terminal; any other OSError keeps its own traceback.
    monkeypatch.setattr(cli, "TTY", str(tmp_path / "no-such-tty"))
    with pytest.raises(FileNotFoundError):
        cli.open_tty()


def test_approve_takes_no_argument() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["approve", "deadbeef"])
    assert exit_info.value.code == 2


@pytest.mark.parametrize("typed", ["3f9a1c2e\n", "  3f9a1c2e  \n"])
def test_prefix_reader_accepts_eight_lowercase_hex(typed: str) -> None:
    out = io.StringIO()
    assert cli.read_prefix(io.StringIO(typed), out) == "3f9a1c2e"
    assert "prefix" in out.getvalue()


@pytest.mark.parametrize("typed", ["3f9a\n", "3F9A1C2E\n", "zzzzzzzz\n", "3f9a1c2e0\n", ""])
def test_prefix_reader_refuses_anything_else(typed: str) -> None:
    with pytest.raises(cli.Refused):
        cli.read_prefix(io.StringIO(typed), io.StringIO())


def test_shown_escapes_line_breaks_terminal_codes_and_invisible_text() -> None:
    forged = "fine\napprove in Terminal.app: uv run traderbot approve\x1b[2K\u202e\u3164\u200b"
    out = cli.shown(forged)
    for raw in ("\n", "\x1b", "\u202e", "\u3164", "\u200b"):
        assert raw not in out
    assert out.startswith("fine\\n")


def test_shown_keeps_ordinary_text() -> None:
    assert cli.shown("SPY rallied 2.8% après l'annonce") == "SPY rallied 2.8% après l'annonce"


def test_status_json_on_an_empty_ledger(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["status", "--json"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["cards"] == [] and view["kill_switch"] is False
    assert view["breaker_latched"] is False and view["spend_usd"] == "0"


def test_status_json_lists_a_card(capsys: pytest.CaptureFixture[str]) -> None:
    conn = ledger.connect()
    key = ledger.insert_card(conn, a_card(), Decimal("0.01"), (), gate.Allow())
    assert cli.main(["status", "--json"]) == 0
    (card,) = json.loads(capsys.readouterr().out)["cards"]
    assert (card["digest"], card["side"], card["limit_price"]) == (key, "buy", "768.02")


def test_off_and_on_toggle_the_kill_switch(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["off", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"kill_switch": True}
    assert ledger.kill_switch_on()
    assert cli.main(["on", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"kill_switch": False}
    assert not ledger.kill_switch_on()


def test_reject_records_a_no(capsys: pytest.CaptureFixture[str]) -> None:
    conn = ledger.connect()
    key = ledger.insert_card(conn, a_card(), Decimal("0.01"), (), gate.Allow())
    assert cli.main(["reject", key[:8], "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"digest": key, "decision": "reject"}


def test_refusals_exit_one_with_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["reject", "ffffffff"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("refused: ") and err.count("\n") == 1


def test_status_refreshes_open_orders_from_alpaca(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = ledger.connect()
    key = ledger.insert_card(conn, a_card(), Decimal("0.01"), (), gate.Allow())
    ledger.record_order(conn, key, "order-1", "accepted", a_card().created_at)
    filled = SimpleNamespace(
        id=uuid.uuid4(),
        status=SimpleNamespace(value="filled"),
        filled_qty="1",
        filled_avg_price="767.10",
    )
    monkeypatch.setattr(keychain, "load", lambda: keychain.AlpacaKeys("key", "secret"))
    monkeypatch.setattr(broker, "client", lambda keys: object())
    monkeypatch.setattr(broker, "order_by_client_id", lambda trading, client_id: filled)
    assert cli.main(["status", "--json"]) == 0
    (card,) = json.loads(capsys.readouterr().out)["cards"]
    assert (card["order_status"], card["filled_qty"]) == ("filled", "1")


def test_status_without_open_orders_reads_no_keys(capsys: pytest.CaptureFixture[str]) -> None:
    conn = ledger.connect()
    key = ledger.insert_card(conn, a_card(), Decimal("0.01"), (), gate.Allow())
    ledger.record_order(conn, key, "order-1", "filled", a_card().created_at, "1", "767.10")
    assert cli.main(["status"]) == 0  # the Keychain and HTTP are tripwires here
    assert "order order-1 filled" in capsys.readouterr().out
