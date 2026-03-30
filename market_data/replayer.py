"""
Deterministic replay harness for L3 market data.
Reads capture logs, synchronizes the simulation clock, maintains an internal
L3 order book to extract top-of-book prices, and feeds fixed-point data to the strategy.
"""

import logging
from typing import Dict, Any

from common.types import MarketEvent
from exchange.models import PRICE_MULTIPLIER
from strategy.base import BaseStrategy
from market_data.reader import (
    read_capture_file,
    is_bitfinex_snapshot,
    is_bitfinex_update,
    extract_bitfinex_snapshot_orders,
    extract_bitfinex_update
)

logger = logging.getLogger(__name__)

class Replayer:
    """
    Orchestrates the replay of historical market data.
    Pipes discrete events into the strategy layer while managing clock sync.
    """

    def __init__(self, filepath: str, strategy: BaseStrategy) -> None:
        self.filepath = filepath
        self.strategy = strategy
        
        # Internal L3 book state to dynamically calculate top-of-book prices
        # Maps order_id (int) -> price (float)
        self._bids: Dict[int, float] = {}
        self._asks: Dict[int, float] = {}

    def run(self, strict: bool = False) -> None:
        """
        Starts the event loop, reading the capture file line by line.
        """
        logger.info(f"Starting replay from {self.filepath}")
        
        for event in read_capture_file(self.filepath, strict=strict):
            self._process_event(event)
            
        logger.info("Replay finished.")

    def _process_event(self, event: MarketEvent) -> None:
        """
        Handles a single MarketEvent: updates clock, rebuilds book, and emits to strategy.
        """
        # 1. Clock Synchronization (Convert ns to ms)
        local_ts_ms = event.local_ts_ns // 1_000_000
        
        # Fallback to local_ts if exchange_ts is 0 (standard Bitfinex R0 behavior)
        if event.exchange_ts_ns and event.exchange_ts_ns > 0:
            exch_ts_ms = event.exchange_ts_ns // 1_000_000
        else:
            exch_ts_ms = local_ts_ms

        self.strategy.update_clock(exch_ts_ms, local_ts_ms)

        # 2. Update Internal Order Book State
        book_updated = False
        
        if is_bitfinex_snapshot(event):
            orders = extract_bitfinex_snapshot_orders(event)
            self._bids.clear()
            self._asks.clear()
            for order in orders:
                if order["side"] == "bid":
                    self._bids[order["order_id"]] = order["price"]
                else:
                    self._asks[order["order_id"]] = order["price"]
            book_updated = True

        elif is_bitfinex_update(event):
            update = extract_bitfinex_update(event)
            if update is None:
                return

            order_id = update["order_id"]
            
            if update["removed"]:
                # Order canceled or fully filled
                self._bids.pop(order_id, None)
                self._asks.pop(order_id, None)
            else:
                # Order added or modified
                if update["side"] == "bid":
                    self._bids[order_id] = update["price"]
                    self._asks.pop(order_id, None)  # Ensure it's not in the wrong side
                elif update["side"] == "ask":
                    self._asks[order_id] = update["price"]
                    self._bids.pop(order_id, None)
            
            book_updated = True

        # 3. Calculate Top of Book and Trigger Strategy Callback
        if book_updated:
            self._emit_market_data(local_ts_ms, exch_ts_ms)

    def _emit_market_data(self, local_ts_ms: int, exch_ts_ms: int) -> None:
        """
        Extracts the best bid/ask and sends human-readable floats to the strategy.
        The BaseStrategy will handle fixed-point integer conversions upon order submission.
        """
        if not self._bids or not self._asks:
            return  

        best_bid_float = max(self._bids.values())
        best_ask_float = min(self._asks.values())

        if best_bid_float >= best_ask_float:
            return

        mid_price_float = (best_bid_float + best_ask_float) / 2.0

        payload = {
            "type": "top_of_book",
            "best_bid": best_bid_float,   # <--- Keep as float
            "best_ask": best_ask_float,   # <--- Keep as float
            "mid_price": mid_price_float, # <--- Keep as float
            "local_ts_ms": local_ts_ms,
            "exchange_ts_ms": exch_ts_ms
        }

        self.strategy.on_market_data(payload)