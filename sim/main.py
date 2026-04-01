"""
End-to-end simulation orchestrator.

Wires the full pipeline:
    Replayer  →  AvellanedaStoikovStrategy  →  NotifyingExchange  →  ExchangeEngine

Usage:
    python -m sim.main <capture_file> [--risk-aversion 0.1] [--order-size 0.01]
"""

import argparse
import logging

from exchange.engine import ExchangeEngine
from exchange.gateway import ExchangeGateway
from sim.notifying_exchange import NotifyingExchange
from strategy.avellaneda_stoikov import AvellanedaStoikovStrategy
from market_data.replayer import Replayer

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("sim.main")


def build_pipeline(
    strategy: AvellanedaStoikovStrategy,
    engine: ExchangeEngine | None = None,
) -> tuple[ExchangeEngine, NotifyingExchange]:
    """Instantiate exchange components and wire them to the strategy."""
    if engine is None:
        engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)

    notifying = NotifyingExchange(
        gateway=gateway,
        on_fill=strategy.on_order_fill,
    )

    strategy.connect_exchange(notifying)
    return engine, notifying


def run(
    capture_file: str,
    risk_aversion: float = 0.1,
    order_size: float = 0.01,
    session_duration: float = 3600.0,
) -> dict:
    """Run a full replay simulation and return a summary dict."""

    strategy = AvellanedaStoikovStrategy(
        risk_aversion=risk_aversion,
        order_size=order_size,
        session_duration=session_duration,
    )

    engine, notifying = build_pipeline(strategy)
    replayer = Replayer(capture_file, strategy)

    logger.info("Pipeline wired: Replayer -> Strategy -> NotifyingExchange -> Engine")
    logger.info("Capture file: %s", capture_file)

    replayer.run()

    account = engine.get_account_state("default")
    summary = {
        "fills": len(notifying.emitted_fills),
        "cash_balance": account.cash_balance,
        "base_position": account.base_position,
        "fees_paid": account.fees_paid,
        "fee_pool": engine.get_fee_pool(),
        "resting_orders": len(engine.book.orders),
        "fill_details": [
            {
                "maker_id": f.maker_order_id,
                "taker_id": f.taker_order_id,
                "price": f.price,
                "quantity": f.quantity,
                "maker_side": f.maker_side.value,
                "taker_side": f.taker_side.value,
            }
            for f in notifying.emitted_fills
        ],
    }

    logger.info(
        "Replay complete — fills=%d, position=%d, fees=%d, resting=%d",
        summary["fills"],
        summary["base_position"],
        summary["fees_paid"],
        summary["resting_orders"],
    )
    return summary


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OrderForge E2E Simulation")
    parser.add_argument("capture_file", help="Path to capture log file")
    parser.add_argument("--risk-aversion", type=float, default=0.1)
    parser.add_argument("--order-size", type=float, default=0.01)
    parser.add_argument("--session-duration", type=float, default=3600.0)
    args = parser.parse_args()

    result = run(
        args.capture_file,
        args.risk_aversion,
        args.order_size,
        args.session_duration,
    )

    print("\n=== Simulation Summary ===")
    for k, v in result.items():
        if k != "fill_details":
            print(f"  {k}: {v}")
    if result["fill_details"]:
        print("\n  Fill log:")
        for i, f in enumerate(result["fill_details"], 1):
            print(
                f"    {i}. {f['taker_side'].upper()} {f['quantity']} @ {f['price']} "
                f"(taker={f['taker_id']}, maker={f['maker_id']})"
            )
