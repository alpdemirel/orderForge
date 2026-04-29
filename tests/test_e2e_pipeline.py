"""
End-to-end integration tests for the full simulation pipeline.

Verifies:
  - Pipeline wiring (Replayer -> Strategy -> NotifyingExchange -> Engine)
  - AS strategy submits orders after sigma is estimated
  - Fills are recorded when liquidity is pre-seeded
  - Account state updates correctly after fills
  - Deterministic replay across runs
"""

import os

import pytest

from exchange.engine import ExchangeEngine
from exchange.gateway import ExchangeGateway
from exchange.models import Order, Side, PRICE_MULTIPLIER, QUANTITY_MULTIPLIER
from market_data.replayer import Replayer
from sim.main import build_pipeline
from sim.notifying_exchange import NotifyingExchange
from strategy.avellaneda_stoikov import AvellanedaStoikovStrategy
from strategy.noop import NoOpStrategy

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
E2E_FIXTURE = os.path.join(FIXTURE_DIR, "e2e_capture_sample.log")


# ── Basic wiring ──────────────────────────────────────────────────────────────


def _make_replayer(strategy, engine, notifying, filepath=E2E_FIXTURE):
    """Helper: create a Replayer wired to the engine and fill listener."""
    return Replayer(filepath, strategy, engine=engine, on_fill=notifying.emit_fill)


class TestPipelineWiring:

    def test_noop_strategy_no_side_effects(self):
        """NoOp receives market data but places no orders."""
        strategy = NoOpStrategy()
        engine, notifying, _ = build_pipeline(strategy, use_latency_queue=False)
        _make_replayer(strategy, engine, notifying).run()

        # Only market orders on the book, no strategy orders, no fills
        assert len(notifying.emitted_fills) == 0

    def test_pipeline_runs_without_exceptions(self):
        """Full pipeline with AS strategy completes without error."""
        strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
        engine, notifying, _ = build_pipeline(strategy, use_latency_queue=False)
        _make_replayer(strategy, engine, notifying).run()


# ── AS strategy order submission ──────────────────────────────────────────────


class TestASStrategySubmitsOrders:

    def test_orders_resting_on_book(self):
        """After replay, strategy should have resting limit orders."""
        strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
        engine, notifying, _ = build_pipeline(strategy, use_latency_queue=False)
        _make_replayer(strategy, engine, notifying).run()

        # Book now has both market + strategy orders
        assert len(engine.book.orders) > 0, "No resting orders after replay"

    def test_active_order_ids_tracked(self):
        """Strategy tracks its last pair of outstanding order IDs."""
        strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
        engine, notifying, _ = build_pipeline(strategy, use_latency_queue=False)
        _make_replayer(strategy, engine, notifying).run()

        assert len(strategy._active_order_ids) == 2

    def test_orders_have_both_sides(self):
        """Strategy places one bid and one ask."""
        strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
        engine, notifying, _ = build_pipeline(strategy, use_latency_queue=False)
        _make_replayer(strategy, engine, notifying).run()

        strat_orders = [
            o for o in engine.book.orders.values()
            if o.owner_id != "__market__"
        ]
        sides = {o.side for o in strat_orders}
        assert Side.BUY in sides, "No BUY order on book"
        assert Side.SELL in sides, "No SELL order on book"


# ── Fills with pre-seeded liquidity ──────────────────────────────────────────


class TestFillsWithMarketLiquidity:
    """Verify fills between strategy orders and market liquidity on the
    unified book.  The e2e fixture has a wide spread (~35 ticks) so the
    AS strategy quotes inside the spread without crossing.  These tests
    verify the mechanism works when a crossing *does* occur."""

    def test_strategy_taker_fill_against_market(self):
        """Strategy submits aggressive buy that crosses market ask on the
        unified book — verifying strategy and market share one LOB."""
        engine = ExchangeEngine()
        # Seed market liquidity directly
        engine.submit_order(Order(
            order_id="mkt_ask", owner_id="__market__", side=Side.SELL,
            price=int(67100 * PRICE_MULTIPLIER),
            quantity=int(0.05 * QUANTITY_MULTIPLIER),
        ))
        strat_buy = Order(
            order_id="strat_agg_buy", owner_id="default", side=Side.BUY,
            price=int(67200 * PRICE_MULTIPLIER),
            quantity=int(0.01 * QUANTITY_MULTIPLIER),
        )
        result = engine.submit_order(strat_buy)
        assert result.accepted
        assert len(result.fills) == 1
        assert result.fills[0].maker_owner_id == "__market__"
        assert result.fills[0].taker_owner_id == "default"

    def test_market_event_fills_resting_strategy_order(self):
        """Market update crosses a resting strategy order → maker fill."""
        engine = ExchangeEngine()
        # Place a strategy sell at 67000
        engine.submit_order(Order(
            order_id="strat_sell_1", owner_id="default", side=Side.SELL,
            price=int(67000 * PRICE_MULTIPLIER),
            quantity=int(0.01 * QUANTITY_MULTIPLIER),
        ))
        # Now apply a market event that adds a bid at 67010 (crosses the strat sell)
        from common.types import MarketEvent
        buy_event = MarketEvent(
            local_ts_ns=999, exchange_ts_ns=0, channel="bf_update",
            raw_json=[1, [9999, 67010, 0.05]],  # bid at 67010
        )
        fills = engine.apply_market_event(buy_event)
        assert len(fills) == 1
        assert fills[0].maker_order_id == "strat_sell_1"
        assert fills[0].maker_side == Side.SELL
        assert fills[0].price == int(67000 * PRICE_MULTIPLIER)

    def test_fill_updates_account_state(self):
        """Verify fill accounting flows correctly for strategy orders."""
        engine = ExchangeEngine()
        # Seed a market sell
        engine.submit_order(Order(
            order_id="mkt_sell", owner_id="__market__", side=Side.SELL,
            price=int(67100 * PRICE_MULTIPLIER),
            quantity=int(0.01 * QUANTITY_MULTIPLIER),
        ))
        # Strategy buys aggressively
        strat_buy = Order(
            order_id="strat_buy_acct", owner_id="strategy_1", side=Side.BUY,
            price=int(67200 * PRICE_MULTIPLIER),
            quantity=int(0.01 * QUANTITY_MULTIPLIER),
        )
        result = engine.submit_order(strat_buy)
        assert len(result.fills) == 1
        account = engine.get_account_state("strategy_1")
        assert account.base_position == int(0.01 * QUANTITY_MULTIPLIER)
        assert account.fees_paid > 0
        assert engine.get_fee_pool() > 0


# ── Determinism ───────────────────────────────────────────────────────────────


class TestDeterminism:

    def test_replay_is_deterministic(self):
        """Two identical runs produce identical results."""
        results = []
        for _ in range(2):
            engine = ExchangeEngine()
            strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
            _, notifying, _ = build_pipeline(strategy, engine=engine, use_latency_queue=False)
            _make_replayer(strategy, engine, notifying).run()

            account = engine.get_account_state("default")
            results.append((
                len(notifying.emitted_fills),
                account.base_position,
                account.cash_balance,
                account.fees_paid,
            ))

        assert results[0] == results[1], f"Non-deterministic: {results[0]} vs {results[1]}"


# ── Orchestrator ──────────────────────────────────────────────────────────────


class TestOrchestrator:

    def test_run_returns_valid_summary(self):
        """sim.main.run() returns a dict with expected keys."""
        from sim.main import run

        summary = run(E2E_FIXTURE, strategy_params={"risk_aversion": 0.1, "order_size": 0.01})

        assert "fills" in summary
        assert "cash_balance_usd" in summary
        assert "position_btc" in summary
        assert "fees_paid_usd" in summary
        assert "total_pnl_usd" in summary
        assert "resting_orders" in summary
        assert summary["resting_orders"] > 0
