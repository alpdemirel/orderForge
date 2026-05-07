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
MARKET_OWNER_ID = "__market__"


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


@dataclass
class Order:
    order_id: str
    side: Side
    price: int
    quantity: int
    owner_id: str = "default"
    tif: TimeInForce = TimeInForce.GTC
    remaining: int = field(init=False)
    status: OrderStatus = field(default=OrderStatus.OPEN, init=False)
    # Monotonic sequence number assigned by OrderBook.add() — used for
    # queue-position modeling.  Orders with a lower book_seq arrived
    # earlier and sit ahead in the FIFO queue at a given price level.
    book_seq: int = field(default=0, init=False)
    # Reservation accounting: how much quote/base is currently held in the
    # owner's locked balance for this order.  Released proportionally on
    # fills and fully on cancel.  Zero for MARKET_OWNER_ID orders.
    locked_quote: int = field(default=0, init=False)
    locked_base: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.remaining = self.quantity


@dataclass
class Fill:
    maker_order_id: str
    taker_order_id: str
    maker_owner_id: str
    taker_owner_id: str
    maker_side: Side
    taker_side: Side
    price: int
    quantity: int


@dataclass
class ExecutionResult:
    accepted: bool
    reason: str = ""
    fills: list[Fill] = field(default_factory=list)
    taker_fee: int = 0
    maker_fees: dict[str, int] = field(default_factory=dict)


@dataclass
class AccountState:
    owner_id: str
    cash_balance: int = 0
    base_position: int = 0
    fees_paid: int = 0
    # Reservations for resting orders. locked_quote is the cash held against
    # open BUYs; locked_base is the inventory held against open SELLs. They
    # are independent of cash_balance / base_position — settlement happens
    # via apply_trade(); locks just gate availability for new orders.
    locked_quote: int = 0
    locked_base: int = 0

    def available_quote(self) -> int:
        return self.cash_balance - self.locked_quote

    def available_base(self) -> int:
        return self.base_position - self.locked_base

    def apply_trade(self, side: Side, price: int, quantity: int, fee: int) -> None:
        notional = price * quantity
        if side == Side.BUY:
            self.base_position += quantity
            self.cash_balance -= notional + fee
        else:
            self.base_position -= quantity
            self.cash_balance += notional - fee
        self.fees_paid += fee
