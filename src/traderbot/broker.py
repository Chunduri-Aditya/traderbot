"""Alpaca paper trading only. Every client is checked against the paper host before use.

The check is a raise, never a bare assert, so `python -O` cannot strip it.
"""

from decimal import Decimal

from alpaca.common.enums import BaseURL
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.models import Order, TradeAccount
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest

from traderbot.keychain import AlpacaKeys
from traderbot.proposal import Proposal

PAPER_URL = BaseURL.TRADING_PAPER  # https://paper-api.alpaca.markets
NOT_FOUND = 404


def assert_paper(client: TradingClient) -> TradingClient:
    # alpaca-py 0.44.0 keeps the host in a private attribute (common/rest.py:65).
    if client._base_url != PAPER_URL:
        raise RuntimeError(f"refusing a trading client on {client._base_url}, want {PAPER_URL}")
    return client


def client(keys: AlpacaKeys) -> TradingClient:
    return assert_paper(TradingClient(keys.key_id, keys.secret, paper=True))


def account(trading: TradingClient) -> TradeAccount:
    result = assert_paper(trading).get_account()
    if not isinstance(result, TradeAccount):
        raise TypeError("account came back raw; the client must return models")
    return result


def buying_power(acct: TradeAccount) -> Decimal:
    if acct.buying_power is None:
        raise RuntimeError("account has no buying_power field")
    return Decimal(acct.buying_power)


def open_order_symbols(trading: TradingClient) -> frozenset[str]:
    request = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
    orders = assert_paper(trading).get_orders(request)
    if not isinstance(orders, list):
        raise TypeError("orders came back raw; the client must return models")
    return frozenset(o.symbol for o in orders if o.symbol)


def order_by_client_id(trading: TradingClient, client_order_id: str) -> Order | None:
    try:
        found = assert_paper(trading).get_order_by_client_id(client_order_id)
    except APIError as err:
        if err.status_code == NOT_FOUND:
            return None
        raise
    if not isinstance(found, Order):
        raise TypeError("order came back raw; the client must return models")
    return found


def submit_limit_buy(trading: TradingClient, card: Proposal, client_order_id: str) -> Order:
    if card.side != "buy" or card.order_type != "limit" or card.time_in_force != "day":
        raise RuntimeError(f"only DAY limit buys are placed, got {card.side} {card.order_type}")
    request = LimitOrderRequest(
        symbol=card.symbol,
        qty=card.qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        limit_price=float(card.limit_price),
        client_order_id=client_order_id,
    )
    placed = assert_paper(trading).submit_order(request)
    if not isinstance(placed, Order):
        raise TypeError("order came back raw; the client must return models")
    return placed
