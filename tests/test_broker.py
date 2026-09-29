"""The paper host check: a live client or an overridden host is refused, paper is allowed."""

import pytest
from alpaca.trading.client import TradingClient

from traderbot import broker
from traderbot.keychain import AlpacaKeys


def test_paper_client_is_allowed() -> None:
    paper = TradingClient("key", "secret", paper=True)
    assert broker.assert_paper(paper) is paper
    assert (
        broker.client(AlpacaKeys("key", "secret"))._base_url == "https://paper-api.alpaca.markets"
    )


def test_live_client_is_refused() -> None:
    with pytest.raises(RuntimeError, match="refusing a trading client on"):
        broker.assert_paper(TradingClient("key", "secret", paper=False))


def test_overridden_host_is_refused() -> None:
    lookalike = "https://paper-api.alpaca.markets.example.com"
    with pytest.raises(RuntimeError, match="refusing a trading client on"):
        broker.assert_paper(TradingClient("key", "secret", paper=True, url_override=lookalike))


def test_keys_never_show_in_repr() -> None:
    assert "hunter2" not in repr(AlpacaKeys("AKID", "hunter2"))
    assert "AKID" not in repr(AlpacaKeys("AKID", "hunter2"))
