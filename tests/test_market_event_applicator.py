"""Regression tests for MarketEventApplicator — the bridge between
Bitfinex L3 market data and the exchange OrderBook."""

import json
import os

import pytest

from common.types import MarketEvent
from exchange.engine import ExchangeEngine
from exchange.gateway import ExchangeGateway
from exchange.market_event_applicator import MarketEventApplicator
from exchange.models import MARKET_OWNER_ID
from exchange.models import Order, Side, TimeInForce, PRICE_MULTIPLIER, QUANTITY_MULTIPLIER
from exchange.order_book import OrderBook
from market_data.reader import read_capture_file, is_bitfinex_snapshot, is_bitfinex_update

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
WS_FIXTURE = os.path.join(FIXTURE_DIR, "events_l3_sample.log")
S3_FIXTURE = os.path.join(FIXTURE_DIR, "snapshot_sample.json")


def _load_ws_events():
    return list(read_capture_file(WS_FIXTURE))


def _load_s3_snapshot():
    with open(S3_FIXTURE, "r", encoding="utf-8") as f:
        return json.load(f)


# ── WebSocket stream path (apply) ───────────────────────────────────────────


class TestWebSocketSnapshot:
    """Tests using events_l3_sample.log (WebSocket capture format)."""

    def test_snapshot_loads_all_orders(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        events = _load_ws_events()
        snapshot = next(e for e in events if is_bitfinex_snapshot(e))
        applicator.apply(snapshot)

        assert len(book.orders) == 3
        assert all(o.owner_id == MARKET_OWNER_ID for o in book.orders.values())

    def test_best_bid_after_snapshot(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        events = _load_ws_events()
        snapshot = next(e for e in events if is_bitfinex_snapshot(e))
        applicator.apply(snapshot)

        # All 3 fixture orders are bids; best bid = highest price
        assert book.best_bid() == round(67001 * PRICE_MULTIPLIER)
        assert book.best_ask() is None

    def test_snapshot_order_quantities(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        events = _load_ws_events()
        snapshot = next(e for e in events if is_bitfinex_snapshot(e))
        applicator.apply(snapshot)

        order = book.orders["mkt-232889975433"]
        assert order.price == round(67001 * PRICE_MULTIPLIER)
        assert order.quantity == round(0.07462561 * QUANTITY_MULTIPLIER)
        assert order.side == Side.BUY

    def test_removal_of_unknown_order_is_graceful(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        events = _load_ws_events()
        snapshot = next(e for e in events if is_bitfinex_snapshot(e))
        applicator.apply(snapshot)

        # First update removes 232889975532 — not in snapshot
        update = [e for e in events if is_bitfinex_update(e)][0]
        applicator.apply(update)
        assert len(book.orders) == 3  # unchanged

    def test_update_adds_new_bid_order(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        events = _load_ws_events()
        snapshot = next(e for e in events if is_bitfinex_snapshot(e))
        applicator.apply(snapshot)

        updates = [e for e in events if is_bitfinex_update(e)]
        # updates[3] adds order 232888163522 at price=66970, amount=0.29807263
        applicator.apply(updates[3])

        assert "mkt-232888163522" in book.orders
        added = book.orders["mkt-232888163522"]
        assert added.side == Side.BUY
        assert added.price == round(66970 * PRICE_MULTIPLIER)
        assert added.quantity == round(0.29807263 * QUANTITY_MULTIPLIER)

    def test_second_snapshot_clears_and_rebuilds(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        events = _load_ws_events()
        snapshot = next(e for e in events if is_bitfinex_snapshot(e))

        applicator.apply(snapshot)
        assert len(book.orders) == 3

        applicator.apply(snapshot)
        assert len(book.orders) == 3  # not 6

    def test_strategy_order_survives_snapshot_clear(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        events = _load_ws_events()
        snapshot = next(e for e in events if is_bitfinex_snapshot(e))

        strategy_order = Order(
            order_id="strat-001",
            side=Side.BUY,
            price=500000,
            quantity=100_000_000,
            owner_id="strategy_1",
        )
        book.add(strategy_order)
        applicator.apply(snapshot)

        assert "strat-001" in book.orders
        assert len(book.orders) == 4  # 3 market + 1 strategy

    def test_non_actionable_channels_are_ignored(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        events = _load_ws_events()

        # First 3 events: bf_info, bf_conf, bf_subscribed
        for event in events[:3]:
            applicator.apply(event)
        assert len(book.orders) == 0

    def test_modify_repositions_order_in_book(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        events = _load_ws_events()
        snapshot = next(e for e in events if is_bitfinex_snapshot(e))
        applicator.apply(snapshot)

        # Simulate modify: same order_id 232889975433 (was at 67001), now at 66998
        modify_event = MarketEvent(
            local_ts_ns=0,
            exchange_ts_ns=0,
            channel="bf_update",
            raw_json=[227546, [232889975433, 66998, 0.07462561]],
        )
        applicator.apply(modify_event)

        order = book.orders.get("mkt-232889975433")
        assert order is not None
        assert order.price == round(66998 * PRICE_MULTIPLIER)
        # Best bid was 67001 (only order at that level); after modify it should be 67000
        assert book.best_bid() == round(67000 * PRICE_MULTIPLIER)


# ── S3 snapshot path (apply_s3_snapshot) ─────────────────────────────────────


class TestS3Snapshot:
    """Tests using snapshot_sample.json (S3 periodic snapshot format)."""

    def test_s3_snapshot_loads_correct_count(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        data = _load_s3_snapshot()
        applicator.apply_s3_snapshot(data)

        assert len(book.orders) == 25
        assert all(o.owner_id == MARKET_OWNER_ID for o in book.orders.values())

    def test_s3_snapshot_best_bid_and_ask(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        data = _load_s3_snapshot()
        applicator.apply_s3_snapshot(data)

        # Best bid = 74041, best ask = 73960 (crossed book from real snapshot)
        assert book.best_bid() == round(74041 * PRICE_MULTIPLIER)
        assert book.best_ask() == round(73960 * PRICE_MULTIPLIER)

    def test_s3_snapshot_bid_and_ask_sides_correct(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        data = _load_s3_snapshot()
        applicator.apply_s3_snapshot(data)

        bids = [o for o in book.orders.values() if o.side == Side.BUY]
        asks = [o for o in book.orders.values() if o.side == Side.SELL]
        assert len(bids) == 11
        assert len(asks) == 14

    def test_s3_snapshot_specific_order(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        data = _load_s3_snapshot()
        applicator.apply_s3_snapshot(data)

        order = book.orders["mkt-233468476364"]
        assert order.side == Side.BUY
        assert order.price == round(74041 * PRICE_MULTIPLIER)
        assert order.quantity == round(0.47140813 * QUANTITY_MULTIPLIER)

    def test_s3_snapshot_clears_previous_market_orders(self):
        engine = ExchangeEngine()
        book = engine.book
        applicator = MarketEventApplicator(engine)
        data = _load_s3_snapshot()

        applicator.apply_s3_snapshot(data)
        assert len(book.orders) == 25

        applicator.apply_s3_snapshot(data)
        assert len(book.orders) == 25  # not 50


# ── Engine and gateway delegation ────────────────────────────────────────────


class TestEngineDelegation:

    def test_engine_apply_market_event(self):
        engine = ExchangeEngine()
        events = _load_ws_events()
        snapshot = next(e for e in events if is_bitfinex_snapshot(e))
        engine.apply_market_event(snapshot)

        assert len(engine.book.orders) == 3
        assert engine.book.best_bid() == round(67001 * PRICE_MULTIPLIER)

    def test_engine_apply_s3_snapshot(self):
        engine = ExchangeEngine()
        data = _load_s3_snapshot()
        engine.apply_s3_snapshot(data)

        assert len(engine.book.orders) == 25
        assert engine.book.best_bid() == round(74041 * PRICE_MULTIPLIER)

    def test_gateway_apply_market_event(self):
        gateway = ExchangeGateway()
        events = _load_ws_events()
        snapshot = next(e for e in events if is_bitfinex_snapshot(e))
        gateway.apply_market_event(snapshot)

        assert gateway._engine.book.best_bid() == round(67001 * PRICE_MULTIPLIER)

    def test_gateway_apply_s3_snapshot(self):
        gateway = ExchangeGateway()
        data = _load_s3_snapshot()
        gateway.apply_s3_snapshot(data)

        assert gateway._engine.book.best_bid() == round(74041 * PRICE_MULTIPLIER)


# ── End-to-end: strategy order fills against market depth ────────────────────


class TestEndToEnd:

    def test_strategy_sell_fills_against_market_bids(self):
        """Load WS fixture snapshot (3 bids at 67001/67000/66999).
        Submit a strategy SELL at 67000 — should fill against best bid (67001)."""
        gateway = ExchangeGateway()
        events = _load_ws_events()
        snapshot = next(e for e in events if is_bitfinex_snapshot(e))
        gateway.apply_market_event(snapshot)

        assert gateway._engine.book.best_bid() == round(67001 * PRICE_MULTIPLIER)

        sell_qty = 100_000  # tiny, guaranteed < any market order
        strategy_sell = Order(
            order_id="strat-sell-001",
            side=Side.SELL,
            price=round(67000 * PRICE_MULTIPLIER),
            quantity=sell_qty,
            owner_id="strategy_1",
            tif=TimeInForce.GTC,
        )
        result = gateway.submit_order(strategy_sell)

        assert result.accepted is True
        assert len(result.fills) == 1
        fill = result.fills[0]
        assert fill.maker_owner_id == MARKET_OWNER_ID
        assert fill.taker_owner_id == "strategy_1"
        assert fill.price == round(67001 * PRICE_MULTIPLIER)
        assert fill.quantity == sell_qty

        strat_account = gateway.get_account_state("strategy_1")
        assert strat_account.base_position == -sell_qty

    def test_strategy_buy_fills_against_s3_snapshot_asks(self):
        """Load S3 fixture snapshot.
        Submit a strategy BUY above best ask — should fill at best ask price."""
        gateway = ExchangeGateway()
        data = _load_s3_snapshot()
        gateway.apply_s3_snapshot(data)

        best_ask = gateway._engine.book.best_ask()
        assert best_ask == round(73960 * PRICE_MULTIPLIER)

        buy_qty = 1000  # tiny
        strategy_buy = Order(
            order_id="strat-buy-001",
            side=Side.BUY,
            price=round(74000 * PRICE_MULTIPLIER),
            quantity=buy_qty,
            owner_id="strategy_1",
            tif=TimeInForce.GTC,
        )
        result = gateway.submit_order(strategy_buy)

        assert result.accepted is True
        assert len(result.fills) >= 1
        fill = result.fills[0]
        assert fill.maker_owner_id == MARKET_OWNER_ID
        assert fill.taker_owner_id == "strategy_1"
        assert fill.price == best_ask

        strat_account = gateway.get_account_state("strategy_1")
        assert strat_account.base_position == buy_qty
