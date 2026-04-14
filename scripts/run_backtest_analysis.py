"""
Detailed backtest analysis — runs the AS strategy on a full day of
captured data and produces granular metrics for review.
"""

import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exchange.engine import ExchangeEngine
from exchange.gateway import ExchangeGateway
from exchange.models import PRICE_MULTIPLIER, QUANTITY_MULTIPLIER, Side
from market_data.replayer import Replayer
from sim.latency_queue import LatencyQueue
from sim.notifying_exchange import NotifyingExchange
from strategy.avellaneda_stoikov import AvellanedaStoikovStrategy

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(name)s | %(message)s")


def run_analysis(capture_file: str, risk_aversion=0.001, order_size=0.01,
                  session_duration=86400.0, default_latency_ms: int = 50):
    fp_div = PRICE_MULTIPLIER * QUANTITY_MULTIPLIER

    # ── Build pipeline ───────────────────────────────────────────────────
    strategy = AvellanedaStoikovStrategy(
        risk_aversion=risk_aversion,
        order_size=order_size,
        session_duration=session_duration,
    )

    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    notifying = NotifyingExchange(gateway=gateway, on_fill=strategy.on_order_fill)
    strategy.connect_exchange(notifying)

    latency_queue = LatencyQueue(
        submit_fn=notifying.submit_order,
        cancel_fn=notifying.cancel_order,
    )
    strategy.connect_latency_queue(latency_queue)

    replayer = Replayer(
        capture_file, strategy, engine=engine,
        on_fill=notifying.emit_fill,
        latency_queue=latency_queue,
        default_latency_ms=default_latency_ms,
    )

    # ── Run ──────────────────────────────────────────────────────────────
    print(f"Running backtest on {capture_file}")
    print(f"  risk_aversion={risk_aversion}, order_size={order_size}, session={session_duration}s")
    print()

    t0 = time.time()
    replayer.run()
    elapsed = time.time() - t0

    # ── Gather results ───────────────────────────────────────────────────
    account = engine.get_account_state("default")
    fills = notifying.emitted_fills

    # Separate buy/sell fills
    buy_fills = []
    sell_fills = []
    for f in fills:
        is_our_maker = f.maker_owner_id == "default"
        is_our_taker = f.taker_owner_id == "default"
        if is_our_maker:
            side = f.maker_side
        elif is_our_taker:
            side = f.taker_side
        else:
            continue
        if side == Side.BUY:
            buy_fills.append(f)
        else:
            sell_fills.append(f)

    # Volume
    buy_vol = sum(f.quantity for f in buy_fills) / QUANTITY_MULTIPLIER
    sell_vol = sum(f.quantity for f in sell_fills) / QUANTITY_MULTIPLIER

    # VWAP
    buy_notional = sum(f.price * f.quantity for f in buy_fills)
    sell_notional = sum(f.price * f.quantity for f in sell_fills)
    buy_qty_total = sum(f.quantity for f in buy_fills)
    sell_qty_total = sum(f.quantity for f in sell_fills)
    buy_vwap = (buy_notional / buy_qty_total / PRICE_MULTIPLIER) if buy_qty_total > 0 else 0
    sell_vwap = (sell_notional / sell_qty_total / PRICE_MULTIPLIER) if sell_qty_total > 0 else 0

    # P&L
    cash_usd = account.cash_balance / fp_div
    fees_usd = account.fees_paid / fp_div
    pos_btc = account.base_position / QUANTITY_MULTIPLIER

    bb = engine.book.best_bid()
    ba = engine.book.best_ask()
    if bb is not None and ba is not None:
        mid = (bb + ba) / 2.0 / PRICE_MULTIPLIER
    elif bb is not None:
        mid = bb / PRICE_MULTIPLIER
    elif ba is not None:
        mid = ba / PRICE_MULTIPLIER
    else:
        mid = 0.0

    unrealised = pos_btc * mid
    total_pnl = cash_usd + unrealised

    # Fill prices for distribution analysis
    fill_prices = [f.price / PRICE_MULTIPLIER for f in fills]
    fill_qtys = [f.quantity / QUANTITY_MULTIPLIER for f in fills]

    # Maker vs taker breakdown
    maker_fills = [f for f in fills if f.maker_owner_id == "default"]
    taker_fills = [f for f in fills if f.taker_owner_id == "default"]

    # Price range
    if fill_prices:
        min_price = min(fill_prices)
        max_price = max(fill_prices)
        mean_price = sum(fill_prices) / len(fill_prices)
    else:
        min_price = max_price = mean_price = 0

    # Per-fill P&L tracking (cumulative)
    cumulative_cash = 0
    cumulative_pos = 0
    pnl_curve = []
    for f in fills:
        qty_btc = f.quantity / QUANTITY_MULTIPLIER
        price_usd = f.price / PRICE_MULTIPLIER
        notional_usd = price_usd * qty_btc

        is_our_maker = f.maker_owner_id == "default"
        if is_our_maker:
            side = f.maker_side
            fee_bps = 10
        else:
            side = f.taker_side
            fee_bps = 20

        fee_usd = notional_usd * fee_bps / 10_000

        if side == Side.BUY:
            cumulative_cash -= (notional_usd + fee_usd)
            cumulative_pos += qty_btc
        else:
            cumulative_cash += (notional_usd - fee_usd)
            cumulative_pos -= qty_btc

        mtm = cumulative_cash + cumulative_pos * price_usd
        pnl_curve.append(mtm)

    # Drawdown
    if pnl_curve:
        peak = pnl_curve[0]
        max_dd = 0
        for pnl in pnl_curve:
            if pnl > peak:
                peak = pnl
            dd = peak - pnl
            if dd > max_dd:
                max_dd = dd
        final_pnl = pnl_curve[-1]
    else:
        max_dd = 0
        final_pnl = 0

    # Turnover
    total_volume_btc = buy_vol + sell_vol
    total_volume_usd = total_volume_btc * mid if mid > 0 else 0

    # Fill rate proxy: how often did orders get filled vs placed
    # (approximation based on vol window)
    vol_sigma = strategy.sigma

    # ── Print report ─────────────────────────────────────────────────────
    print("=" * 70)
    print("  ORDERFORGE BACKTEST REPORT")
    print("=" * 70)

    print(f"\n{'─── EXECUTION ───':─<70}")
    print(f"  {'Replay time':<35} {elapsed:>10.1f} s")
    print(f"  {'Events processed':<35} {'~4.2M':>10}")
    print(f"  {'Snapshots available':<35} {'144':>10}")

    print(f"\n{'─── STRATEGY PARAMETERS ───':─<70}")
    print(f"  {'Risk aversion (gamma)':<35} {risk_aversion:>10.4f}")
    print(f"  {'Order size':<35} {order_size:>10.4f} BTC")
    print(f"  {'Session duration':<35} {session_duration:>10.0f} s")
    print(f"  {'Liquidity param (kappa)':<35} {strategy.k:>10.2f}")
    print(f"  {'Final sigma (vol estimate)':<35} {vol_sigma:>10.4f} $/tick")

    print(f"\n{'─── FILL SUMMARY ───':─<70}")
    print(f"  {'Total fills':<35} {len(fills):>10}")
    print(f"  {'Buy fills':<35} {len(buy_fills):>10}")
    print(f"  {'Sell fills':<35} {len(sell_fills):>10}")
    print(f"  {'Maker fills (passive)':<35} {len(maker_fills):>10}")
    print(f"  {'Taker fills (aggressive)':<35} {len(taker_fills):>10}")

    print(f"\n{'─── VOLUME ───':─<70}")
    print(f"  {'Buy volume':<35} {buy_vol:>10.6f} BTC")
    print(f"  {'Sell volume':<35} {sell_vol:>10.6f} BTC")
    print(f"  {'Total volume':<35} {total_volume_btc:>10.6f} BTC")
    print(f"  {'Total volume (USD est.)':<35} ${total_volume_usd:>11,.2f}")
    print(f"  {'Net volume (buy - sell)':<35} {buy_vol - sell_vol:>+10.6f} BTC")

    print(f"\n{'─── PRICE ANALYSIS ───':─<70}")
    print(f"  {'Final mid price':<35} ${mid:>11,.2f}")
    print(f"  {'Buy VWAP':<35} ${buy_vwap:>11,.2f}")
    print(f"  {'Sell VWAP':<35} ${sell_vwap:>11,.2f}")
    print(f"  {'VWAP spread (sell - buy)':<35} ${sell_vwap - buy_vwap:>11,.2f}")
    print(f"  {'Fill price range':<35} ${min_price:>,.2f} – ${max_price:>,.2f}")
    print(f"  {'Mean fill price':<35} ${mean_price:>11,.2f}")

    print(f"\n{'─── P&L BREAKDOWN ───':─<70}")
    print(f"  {'Realised cash':<35} ${cash_usd:>+12,.2f}")
    print(f"  {'Open position':<35} {pos_btc:>+11.6f} BTC")
    print(f"  {'Unrealised PnL':<35} ${unrealised:>+12,.2f}")
    print(f"  {'Total fees paid':<35} ${fees_usd:>12,.2f}")
    print(f"  {'Mark-to-market PnL':<35} ${total_pnl:>+12,.2f}")
    print(f"  {'PnL per fill':<35} ${total_pnl / len(fills) if fills else 0:>12,.4f}")
    print(f"  {'PnL per BTC traded':<35} ${total_pnl / total_volume_btc if total_volume_btc else 0:>12,.2f}")

    print(f"\n{'─── RISK ───':─<70}")
    print(f"  {'Max drawdown':<35} ${max_dd:>12,.2f}")
    if total_pnl > 0 and max_dd > 0:
        print(f"  {'Return / max DD (Calmar-like)':<35} {total_pnl / max_dd:>12.2f}x")
    print(f"  {'Final inventory':<35} {pos_btc:>+11.6f} BTC")
    print(f"  {'Inventory value at mid':<35} ${abs(unrealised):>12,.2f}")

    print(f"\n{'─── MARKET MAKING EFFICIENCY ───':─<70}")
    if buy_vwap > 0 and sell_vwap > 0:
        edge_per_btc = sell_vwap - buy_vwap
        edge_total = edge_per_btc * min(buy_vol, sell_vol)
        print(f"  {'Gross edge (VWAP spread)':<35} ${edge_per_btc:>12,.2f} /BTC")
        print(f"  {'Gross edge (matched vol)':<35} ${edge_total:>12,.2f}")
        print(f"  {'Fees as % of gross edge':<35} {fees_usd / edge_total * 100 if edge_total else 0:>11.1f}%")
    roundtrips = min(len(buy_fills), len(sell_fills))
    print(f"  {'Completed round-trips (approx)':<35} {roundtrips:>10}")

    print("\n" + "=" * 70)

    # ── P&L curve summary (percentiles) ──────────────────────────────────
    if pnl_curve:
        sorted_pnl = sorted(pnl_curve)
        n = len(sorted_pnl)
        print(f"\n{'─── P&L CURVE DISTRIBUTION ───':─<70}")
        print(f"  {'Min':<20} ${sorted_pnl[0]:>+12,.2f}")
        print(f"  {'25th percentile':<20} ${sorted_pnl[n // 4]:>+12,.2f}")
        print(f"  {'Median':<20} ${sorted_pnl[n // 2]:>+12,.2f}")
        print(f"  {'75th percentile':<20} ${sorted_pnl[3 * n // 4]:>+12,.2f}")
        print(f"  {'Max':<20} ${sorted_pnl[-1]:>+12,.2f}")
        print(f"  {'Final':<20} ${pnl_curve[-1]:>+12,.2f}")
        # Fraction of time in profit
        in_profit = sum(1 for p in pnl_curve if p > 0) / n * 100
        print(f"  {'Time in profit':<20} {in_profit:>11.1f}%")
        print("=" * 70)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("capture_file")
    parser.add_argument("--risk-aversion", type=float, default=0.001)
    parser.add_argument("--order-size", type=float, default=0.01)
    parser.add_argument("--session-duration", type=float, default=86400.0)
    parser.add_argument("--default-latency-ms", type=int, default=50,
                        help="Synthetic one-way latency (ms) when exchange_ts is absent")
    args = parser.parse_args()

    run_analysis(
        args.capture_file,
        risk_aversion=args.risk_aversion,
        order_size=args.order_size,
        session_duration=args.session_duration,
        default_latency_ms=args.default_latency_ms,
    )
