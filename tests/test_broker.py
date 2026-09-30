"""The paper host check: a live client or an overridden host is refused, paper is allowed."""

from decimal import Decimal
from typing import cast

import pytest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest
from support import a_card

from traderbot import broker, ledger
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


def as_client(fake: "FakeTrading") -> TradingClient:
    return cast(TradingClient, fake)


class FakeTrading:
    """A stand in client on the paper host that records what would have been submitted."""

    _base_url = broker.PAPER_URL

    def __init__(self) -> None:
        self.submitted: list[object] = []

    def submit_order(self, request: object) -> object:
        self.submitted.append(request)
        return request


def test_kill_switch_refuses_a_submit_even_called_directly() -> None:
    ledger.set_kill_switch(True)
    fake = FakeTrading()
    with pytest.raises(broker.KillSwitchOn):
        broker.submit_limit_order(as_client(fake), a_card(), "a" * 64)
    assert fake.submitted == []


def test_sell_and_buy_requests_carry_the_card_fields() -> None:
    fake = FakeTrading()
    with pytest.raises(TypeError):  # the fake returns the request, not an Order
        broker.submit_limit_order(
            as_client(fake), a_card(side="sell", limit_price=Decimal("760.38")), "b" * 64
        )
    (request,) = fake.submitted
    assert isinstance(request, LimitOrderRequest)
    assert (request.side, request.qty, request.limit_price) == (OrderSide.SELL, 1, 760.38)
    assert (request.time_in_force, request.client_order_id) == (TimeInForce.DAY, "b" * 64)


def test_hold_is_never_submitted() -> None:
    fake = FakeTrading()
    with pytest.raises(RuntimeError, match="only DAY limit buys and sells"):
        broker.submit_limit_order(as_client(fake), a_card(side="hold"), "c" * 64)
    assert fake.submitted == []
