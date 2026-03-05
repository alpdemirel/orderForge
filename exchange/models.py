"""Core exchange domain objects for the order book engine skeleton."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class TimeInForce(str, Enum):
    GTC = "gtc"
    IOC = "ioc"
    POST_ONLY = "post_only"


class OrderStatus(str, Enum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass(slots=True)
class Order:
    order_id: str
    side: Side
    price: float
    quantity: float
    tif: TimeInForce = TimeInForce.GTC
    remaining: float = field(init=False)
    status: OrderStatus = field(default=OrderStatus.OPEN, init=False)

    def __post_init__(self) -> None:
        self.remaining = self.quantity


@dataclass(slots=True)
class Fill:
    maker_order_id: str
    taker_order_id: str
    price: float
    quantity: float


@dataclass(slots=True)
class ExecutionResult:
    accepted: bool
    reason: str = ""
    fills: list[Fill] = field(default_factory=list)

