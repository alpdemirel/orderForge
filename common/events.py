from dataclasses import dataclass
from typing import Literal, Optional

@dataclass
class NormalizedEvent:
    """Base class for all normalized market events."""
    exchange_ts_ns: int
    local_ts_ns: int
    product_id: str

@dataclass
class OrderUpdate(NormalizedEvent):
    """
    Represents an incremental change or addition to the order book.
    Bitfinex L3 events include order_id, side, price and amount.
    """
    order_id: int
    side: Literal["bid", "ask"]
    price: float
    amount: float

@dataclass
class OrderCancel(NormalizedEvent):
    """
    Represents an order removal from the order book.
    Triggered when Bitfinex PRICE == 0.
    """
    order_id: int
