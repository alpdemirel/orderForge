"""
Deterministic replay harness for L3 market data.

Reads capture logs, synchronises the simulation clock, feeds L3 events
into the ExchangeEngine (single source of truth for the order book),
extracts top-of-book prices, and drives strategy callbacks.
"""

import logging
from typing import Callable, Optional

from common.types import MarketEvent
from exchange.models import Fill, PRICE_MULTIPLIER
from market_data.reader import (
    read_capture_file,
    is_bitfinex_snapshot,
    is_bitfinex_update,
)
from strategy.base import BaseStrategy

logger = logging.getLogger(__name__)


class Replayer:
    """
    Orchestrates the replay of historical market data.

    When *engine* is provided every L3 event is applied to the exchange's
    order book so that strategy orders compete against real historical
    liquidity.  Top-of-book prices are derived from the engine's book
    (fixed-point ints converted back to floats for the strategy layer).
    """

    def __init__(
        self,
        filepath: str,
        strategy: BaseStrategy,
        engine=None,
        on_fill: Optional[Callable[[Fill], None]] = None,
        latency_queue=None,
        default_latency_ms: int = 0,
        on_market_data: Optional[Callable] = None,
    ) -> None:
        self.filepath = filepath
        self.strategy = strategy
        self.engine = engine
        self.on_fill = on_fill
        self.latency_queue = latency_queue
        self.default_latency_ms = default_latency_ms
        self.on_market_data = on_market_data

    def run(self, strict: bool = False) -> None:
        logger.info(f"Starting replay from {self.filepath}")
        for event in read_capture_file(self.filepath, strict=strict):
            self._process_event(event)
        logger.info("Replay finished.")

    def _process_event(self, event: MarketEvent) -> None:
        # 1. Clock synchronisation (ns → ms)
        local_ts_ms = event.local_ts_ns // 1_000_000

        if event.exchange_ts_ns and event.exchange_ts_ns > 0:
            exch_ts_ms = event.exchange_ts_ns // 1_000_000
        elif self.default_latency_ms > 0:
            # No exchange timestamp available — synthesise one by
            # subtracting the configured default latency from local_ts.
            # This makes the strategy compute:
            #   latency_estimate = local_ts - exch_ts = default_latency_ms
            #   landing_ts = local_ts + default_latency_ms
            exch_ts_ms = local_ts_ms - self.default_latency_ms
        else:
            exch_ts_ms = local_ts_ms

        # Guard: if clocks are skewed and local_ts < exch_ts, clamp latency
        # to zero rather than letting a negative value propagate into the
        # latency predictor.
        if local_ts_ms < exch_ts_ms:
            logger.warning(
                "Negative latency detected (local=%d < exch=%d), clamping exch_ts to local_ts",
                local_ts_ms, exch_ts_ms,
            )
            exch_ts_ms = local_ts_ms

        self.strategy.update_clock(exch_ts_ms, local_ts_ms)

        # 2. Drain latency queue — deliver strategy orders that have "arrived"
        #    Note: fills from drained orders are already emitted by
        #    NotifyingExchange.submit_order() → _emit_fills(), so we must
        #    NOT re-emit them here (that would double-count inventory and
        #    duplicate entries in emitted_fills).
        if self.latency_queue is not None:
            self.latency_queue.drain(exch_ts_ms)

        # 3. Feed event into the exchange engine (unified book)
        book_updated = False
        if self.engine and (is_bitfinex_snapshot(event) or is_bitfinex_update(event)):
            fills = self.engine.apply_market_event(event)
            book_updated = True

            # Route market-originated strategy fills back to the strategy
            if fills and self.on_fill:
                for fill in fills:
                    self.on_fill(fill)

        # 4. Emit top-of-book to the strategy
        if book_updated:
            self._emit_market_data(local_ts_ms, exch_ts_ms)

    def _emit_market_data(self, local_ts_ms: int, exch_ts_ms: int) -> None:
        """Extract best bid/ask from the engine book and send floats to strategy."""
        if self.engine is None:
            return

        best_bid_int = self.engine.book.best_bid()
        best_ask_int = self.engine.book.best_ask()

        if best_bid_int is None or best_ask_int is None:
            return

        best_bid_float = best_bid_int / PRICE_MULTIPLIER
        best_ask_float = best_ask_int / PRICE_MULTIPLIER

        if best_bid_float >= best_ask_float:
            return

        mid_price_float = (best_bid_float + best_ask_float) / 2.0

        # Depth snapshot: top 5 price levels on each side (fixed-point → float)
        depth = self.engine.book.depth_snapshot(levels=5)
        bids_float = [(p / PRICE_MULTIPLIER, q) for p, q in depth["bids"]]
        asks_float = [(p / PRICE_MULTIPLIER, q) for p, q in depth["asks"]]

        payload = {
            "type": "top_of_book",
            "best_bid": best_bid_float,
            "best_ask": best_ask_float,
            "mid_price": mid_price_float,
            "local_ts_ms": local_ts_ms,
            "exchange_ts_ms": exch_ts_ms,
            "depth_bids": bids_float,
            "depth_asks": asks_float,
        }

        self.strategy.on_market_data(payload)

        # Optional spread-series collector — used by sim/analysis.py for charts.
        if self.on_market_data is not None:
            self.on_market_data(
                local_ts_ms, exch_ts_ms,
                best_bid_float, best_ask_float, mid_price_float,
            )