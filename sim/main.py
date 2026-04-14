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
from sim.latency_queue import LatencyQueue
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
    use_latency_queue: bool = True,
) -> tuple[ExchangeEngine, NotifyingExchange, LatencyQueue | None]:
    """Instantiate exchange components and wire them to the strategy."""
    if engine is None:
        engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)

    notifying = NotifyingExchange(
        gateway=gateway,
        on_fill=strategy.on_order_fill,
    )

    strategy.connect_exchange(notifying)

    latency_queue = None
    if use_latency_queue:
        latency_queue = LatencyQueue(
            submit_fn=notifying.submit_order,
            cancel_fn=notifying.cancel_order,
        )
        strategy.connect_latency_queue(latency_queue)

    return engine, notifying, latency_queue


def run(
    capture_file: str,
    risk_aversion: float = 0.1,
    order_size: float = 0.01,
    session_duration: float = 3600.0,
    default_latency_ms: int = 50,
) -> dict:
    """Run a full replay simulation and return a summary dict."""

    strategy = AvellanedaStoikovStrategy(
        risk_aversion=risk_aversion,
        order_size=order_size,
        session_duration=session_duration,
    )

    engine, notifying, latency_queue = build_pipeline(strategy)
    replayer = Replayer(
        capture_file, strategy, engine=engine, on_fill=notifying.emit_fill,
        latency_queue=latency_queue,
        default_latency_ms=default_latency_ms,
    )

    logger.info("Pipeline wired: Replayer -> Engine -> Strategy -> NotifyingExchange")
    logger.info("Capture file: %s", capture_file)

    replayer.run()


    from exchange.models import PRICE_MULTIPLIER, QUANTITY_MULTIPLIER

    account = engine.get_account_state("default")

    # Convert fixed-point to human-readable
    fp_divisor = PRICE_MULTIPLIER * QUANTITY_MULTIPLIER  # price_ticks * satoshis → USD
    cash_usd = account.cash_balance / fp_divisor
    fees_usd = account.fees_paid / fp_divisor
    position_btc = account.base_position / QUANTITY_MULTIPLIER

    # Mark-to-market: value unrealised position at final mid-price
    bb = engine.book.best_bid()
    ba = engine.book.best_ask()
    if bb is not None and ba is not None:
        mid_price_usd = (bb + ba) / 2.0 / PRICE_MULTIPLIER
    elif bb is not None:
        mid_price_usd = bb / PRICE_MULTIPLIER
    elif ba is not None:
        mid_price_usd = ba / PRICE_MULTIPLIER
    else:
        mid_price_usd = 0.0

    unrealised_usd = position_btc * mid_price_usd
    total_pnl_usd = cash_usd + unrealised_usd

    # Per-fill breakdown for detailed analysis
    buy_fills = [f for f in notifying.emitted_fills if
                 (f.maker_owner_id == "default" and f.maker_side.value == "buy") or
                 (f.taker_owner_id == "default" and f.taker_side.value == "buy")]
    sell_fills = [f for f in notifying.emitted_fills if
                  (f.maker_owner_id == "default" and f.maker_side.value == "sell") or
                  (f.taker_owner_id == "default" and f.taker_side.value == "sell")]

    buy_volume_btc = sum(f.quantity for f in buy_fills) / QUANTITY_MULTIPLIER
    sell_volume_btc = sum(f.quantity for f in sell_fills) / QUANTITY_MULTIPLIER

    summary = {
        "fills": len(notifying.emitted_fills),
        "buy_fills": len(buy_fills),
        "sell_fills": len(sell_fills),
        "buy_volume_btc": buy_volume_btc,
        "sell_volume_btc": sell_volume_btc,
        "cash_balance_usd": cash_usd,
        "position_btc": position_btc,
        "fees_paid_usd": fees_usd,
        "unrealised_pnl_usd": unrealised_usd,
        "total_pnl_usd": total_pnl_usd,
        "mid_price_usd": mid_price_usd,
        "resting_orders": len(engine.book.orders),
        # Raw fixed-point for programmatic use
        "cash_balance_raw": account.cash_balance,
        "base_position_raw": account.base_position,
        "fees_paid_raw": account.fees_paid,
    }

    logger.info(
        "Replay complete — fills=%d, position=%.6f BTC, PnL=$%.2f, fees=$%.2f",
        summary["fills"],
        summary["position_btc"],
        summary["total_pnl_usd"],
        summary["fees_paid_usd"],
    )
    return summary


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OrderForge E2E Simulation")
    parser.add_argument("capture_file", help="Path to capture log file")
    parser.add_argument("--risk-aversion", type=float, default=0.001)
    parser.add_argument("--order-size", type=float, default=0.01)
    parser.add_argument("--session-duration", type=float, default=86400.0)
    parser.add_argument("--default-latency-ms", type=int, default=50,
                        help="Synthetic one-way latency (ms) when exchange_ts is absent")
    args = parser.parse_args()

    result = run(
        args.capture_file,
        args.risk_aversion,
        args.order_size,
        args.session_duration,
        args.default_latency_ms,
    )

    print("\n" + "=" * 60)
    print("  SIMULATION RESULTS")
    print("=" * 60)
    print(f"  {'Total fills':<30} {result['fills']:>12}")
    print(f"  {'Buy fills':<30} {result['buy_fills']:>12}")
    print(f"  {'Sell fills':<30} {result['sell_fills']:>12}")
    print(f"  {'Buy volume':<30} {result['buy_volume_btc']:>11.6f} BTC")
    print(f"  {'Sell volume':<30} {result['sell_volume_btc']:>11.6f} BTC")
    print()
    print(f"  {'Net position':<30} {result['position_btc']:>11.6f} BTC")
    print(f"  {'Mark price':<30} ${result['mid_price_usd']:>11,.2f}")
    print()
    print(f"  {'Realised cash':<30} ${result['cash_balance_usd']:>11,.2f}")
    print(f"  {'Unrealised PnL':<30} ${result['unrealised_pnl_usd']:>11,.2f}")
    print(f"  {'Fees paid':<30} ${result['fees_paid_usd']:>11,.2f}")
    print(f"  {'Total PnL (net of fees)':<30} ${result['total_pnl_usd']:>11,.2f}")
    print(f"  {'Resting orders':<30} {result['resting_orders']:>12}")
    print("=" * 60)
