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


class TestPipelineWiring:

    def test_noop_strategy_no_side_effects(self):
        """NoOp receives market data but places no orders."""
        strategy = NoOpStrategy()
        engine, notifying = build_pipeline(strategy)
        Replayer(E2E_FIXTURE, strategy).run()

        assert len(engine.book.orders) == 0
        assert len(notifying.emitted_fills) == 0

    def test_pipeline_runs_without_exceptions(self):
        """Full pipeline with AS strategy completes without error."""
        strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
        build_pipeline(strategy)
        Replayer(E2E_FIXTURE, strategy).run()


# ── AS strategy order submission ──────────────────────────────────────────────


class TestASStrategySubmitsOrders:

    def test_orders_resting_on_book(self):
        """After replay, strategy should have resting limit orders."""
        strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
        engine, _ = build_pipeline(strategy)
        Replayer(E2E_FIXTURE, strategy).run()

        assert len(engine.book.orders) > 0, "No resting orders after replay"

    def test_active_order_ids_tracked(self):
        """Strategy tracks its last pair of outstanding order IDs."""
        strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
        build_pipeline(strategy)
        Replayer(E2E_FIXTURE, strategy).run()

        assert len(strategy._active_order_ids) == 2

    def test_orders_have_both_sides(self):
        """Strategy places one bid and one ask."""
        strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
        engine, _ = build_pipeline(strategy)
        Replayer(E2E_FIXTURE, strategy).run()

        sides = {o.side for o in engine.book.orders.values()}
        assert Side.BUY in sides, "No BUY order on book"
        assert Side.SELL in sides, "No SELL order on book"


# ── Fills with pre-seeded liquidity ──────────────────────────────────────────


def _seeded_engine() -> ExchangeEngine:
    """Engine with a sell order the AS bid will cross (sell at 67000)."""
    engine = ExchangeEngine()
    engine.submit_order(
        Order(
            order_id="seed_sell_1",
            owner_id="liquidity",
            side=Side.SELL,
            price=int(67000 * PRICE_MULTIPLIER),
            quantity=int(0.1 * QUANTITY_MULTIPLIER),
        )
    )
    return engine


class TestFillsWithLiquidity:

    def test_fills_produced(self):
        """AS bid crosses seeded sell -> fills emitted."""
        strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
        _, notifying = build_pipeline(strategy, engine=_seeded_engine())
        Replayer(E2E_FIXTURE, strategy).run()

        assert len(notifying.emitted_fills) > 0, "No fills produced"

    def test_fill_updates_inventory(self):
        """on_order_fill callback changes strategy inventory."""
        strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
        _, notifying = build_pipeline(strategy, engine=_seeded_engine())
        Replayer(E2E_FIXTURE, strategy).run()

        if notifying.emitted_fills:
            assert strategy.inventory_position != 0.0

    def test_account_state_updated(self):
        """Exchange account reflects positions and fees after fills."""
        engine = _seeded_engine()
        strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
        _, notifying = build_pipeline(strategy, engine=engine)
        Replayer(E2E_FIXTURE, strategy).run()

        if notifying.emitted_fills:
            account = engine.get_account_state("default")
            assert account.base_position != 0
            assert account.fees_paid >= 0

    def test_fee_pool_grows(self):
        """Fee pool is positive after fills."""
        engine = _seeded_engine()
        strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
        _, notifying = build_pipeline(strategy, engine=engine)
        Replayer(E2E_FIXTURE, strategy).run()

        if notifying.emitted_fills:
            assert engine.get_fee_pool() > 0


# ── Determinism ───────────────────────────────────────────────────────────────


class TestDeterminism:

    def test_replay_is_deterministic(self):
        """Two identical runs produce identical results."""
        results = []
        for _ in range(2):
            engine = _seeded_engine()
            strategy = AvellanedaStoikovStrategy(risk_aversion=0.1, order_size=0.01)
            _, notifying = build_pipeline(strategy, engine=engine)
            Replayer(E2E_FIXTURE, strategy).run()

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

        summary = run(E2E_FIXTURE, risk_aversion=0.1, order_size=0.01)

        assert "fills" in summary
        assert "cash_balance" in summary
        assert "base_position" in summary
        assert "fees_paid" in summary
        assert "fee_pool" in summary
        assert "resting_orders" in summary
        assert isinstance(summary["fill_details"], list)
        assert summary["resting_orders"] > 0
