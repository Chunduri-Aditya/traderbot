"""Alpaca paper keys from the macOS Keychain. The values never reach a log, argv, or repr."""

import subprocess
from dataclasses import dataclass, field

SERVICE = "traderbot-alpaca-paper"
SECURITY_BIN = "/usr/bin/security"  # the binary that created the items, so no Keychain prompt
TIMEOUT_S = 10


@dataclass(frozen=True)
class AlpacaKeys:
    key_id: str = field(repr=False)
    secret: str = field(repr=False)


def _read(account: str) -> str:
    argv = [SECURITY_BIN, "find-generic-password", "-s", SERVICE, "-a", account, "-w"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        # `from None` drops the timeout exception, whose .stdout may hold part of the value.
        raise RuntimeError(f"Keychain read of {SERVICE}/{account} timed out") from None
    if proc.returncode != 0:
        # stderr names the item, never the value; the value is only ever on stdout.
        raise RuntimeError(
            f"Keychain item {SERVICE}/{account} not readable (security exit {proc.returncode})"
        )
    value = proc.stdout.strip()
    if not value:
        raise RuntimeError(f"Keychain item {SERVICE}/{account} is empty")
    return value


def load() -> AlpacaKeys:
    return AlpacaKeys(key_id=_read("key_id"), secret=_read("secret"))
