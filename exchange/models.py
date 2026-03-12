"""Core exchange domain objects for the order book engine skeleton."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Fixed-point conventions (financial systems best practice — avoid float drift)
# Price: 1 tick = $0.1 USD → multiply by 10. Example: $43250.5 → 432505
# Quantity: 1 satoshi = 0.00000001 BTC → multiply by 100_000_000. Example: 1.5 BTC → 150_000_000
# Fee notional = price_ticks * satoshis. For display USD: divide by PRICE_MULTIPLIER * QUANTITY_MULTIPLIER
PRICE_MULTIPLIER = 10
QUANTITY_MULTIPLIER = 100_000_000


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class TimeInForce(str, Enum):
    GTC = "gtc"   # Good Till Canceled — stays on the book until filled or explicitly canceled
    IOC = "ioc" # Immediate Or Cancel - fills as much as possible and cancels the rest
    POST_ONLY = "post_only" # Post Only - Must rest on the book — reject if it would immediately match  


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
    price: int
    quantity: int
    owner_id: str = "default"
    tif: TimeInForce = TimeInForce.GTC
    remaining: int = field(init=False)
    status: OrderStatus = field(default=OrderStatus.OPEN, init=False)

    def __post_init__(self) -> None:
        self.remaining = self.quantity


@dataclass(slots=True)
class Fill:
    maker_order_id: str
    taker_order_id: str
    maker_owner_id: str
    taker_owner_id: str
    maker_side: Side
    taker_side: Side
    price: int
    quantity: int


@dataclass(slots=True)
class ExecutionResult:
    accepted: bool
    reason: str = ""
    fills: list[Fill] = field(default_factory=list)
    taker_fee: int = 0
    maker_fees: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class AccountState:
    owner_id: str
    cash_balance: int = 0
    base_position: int = 0
    fees_paid: int = 0

    def apply_trade(self, side: Side, price: int, quantity: int, fee: int) -> None:
        notional = price * quantity
        if side == Side.BUY:
            self.base_position += quantity
            self.cash_balance -= notional + fee
        else:
            self.base_position -= quantity
            self.cash_balance += notional - fee
        self.fees_paid += fee
