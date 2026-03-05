from dataclasses import dataclass
from typing import Literal

@dataclass
class NormalizedEvent:
    """Base class for all normalized market events."""
    local_ts_ns: int
    product_id: str

@dataclass
class OrderBookSnapshot(NormalizedEvent):
    """
    Represents the initial state of the order book.
    bids/asks are lists of (price, size) tuples as strings or floats.
    """
    bids: list[tuple[str, str]]
    asks: list[tuple[str, str]]

@dataclass
class OrderUpdate(NormalizedEvent):
    """
    Represents an incremental change to the order book.
    Commonly mapped from 'l2update' channels.
    """
    side: Literal["buy", "sell"]
    price: str
    size: str  # New size at this price level
