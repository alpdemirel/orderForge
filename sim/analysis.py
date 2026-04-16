"""
Reusable backtest analysis module.

Runs the AS strategy on captured data and returns structured results
(summary metrics + time-series for charts) as a dict suitable for JSON
serialisation by the API layer.
"""

import math
import time
from typing import Optional

from exchange.engine import ExchangeEngine
from exchange.gateway import ExchangeGateway
from exchange.models import PRICE_MULTIPLIER, QUANTITY_MULTIPLIER, Side
from market_data.replayer import Replayer
from sim.latency_queue import LatencyQueue
from sim.notifying_exchange import NotifyingExchange
from strategy.avellaneda_stoikov import AvellanedaStoikovStrategy

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s | %(name)s | %(message)s")


def run_full_analysis(
    capture_file: str,
    risk_aversion: float = 0.001,
    order_size: float = 0.01,
    session_duration: float = 86400.0,
    default_latency_ms: int = 50,
) -> dict:
    """Run a full replay and return structured results for the UI."""

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

    # Spread time-series collector — sampled during replay
    spread_series: list[dict] = []
    _event_counter = [0]
    _SPREAD_SAMPLE_INTERVAL = 1000  # record every N events

    original_emit = notifying.emit_fill.__func__ if hasattr(notifying.emit_fill, '__func__') else None

    def spread_callback(local_ts_ms: int, exch_ts_ms: int, best_bid: float, best_ask: float, mid: float):
        _event_counter[0] += 1
        if _event_counter[0] % _SPREAD_SAMPLE_INTERVAL == 0:
            spread_series.append({
                "timestamp_ms": exch_ts_ms,
                "bid_price": best_bid,
                "ask_price": best_ask,
                "mid_price": mid,
            })

    replayer = Replayer(
        capture_file, strategy, engine=engine,
        on_fill=notifying.emit_fill,
        latency_queue=latency_queue,
        default_latency_ms=default_latency_ms,
        on_market_data=spread_callback,
    )

    # ── Run ──────────────────────────────────────────────────────────────
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

    # Maker vs taker breakdown
    maker_fills = [f for f in fills if f.maker_owner_id == "default"]
    taker_fills = [f for f in fills if f.taker_owner_id == "default"]

    # Fill prices for distribution
    fill_prices = [f.price / PRICE_MULTIPLIER for f in fills]
    if fill_prices:
        min_price = min(fill_prices)
        max_price = max(fill_prices)
        mean_price = sum(fill_prices) / len(fill_prices)
    else:
        min_price = max_price = mean_price = 0

    # ── Time-series for charts ───────────────────────────────────────────

    # PnL curve + inventory curve + fill scatter (all per-fill)
    pnl_curve = []
    inventory_curve = []
    fill_scatter = []
    cumulative_cash = 0
    cumulative_pos = 0

    for i, f in enumerate(fills):
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

        pnl_curve.append({"fill_index": i, "pnl": round(mtm, 4)})
        inventory_curve.append({"fill_index": i, "position_btc": round(cumulative_pos, 8)})
        fill_scatter.append({
            "fill_index": i,
            "price": price_usd,
            "side": side.value,
            "quantity": qty_btc,
        })

    # Drawdown
    if pnl_curve:
        peak = pnl_curve[0]["pnl"]
        max_dd = 0
        for p in pnl_curve:
            if p["pnl"] > peak:
                peak = p["pnl"]
            dd = peak - p["pnl"]
            if dd > max_dd:
                max_dd = dd
    else:
        max_dd = 0

    # Volume totals
    total_volume_btc = buy_vol + sell_vol
    total_volume_usd = total_volume_btc * mid if mid > 0 else 0

    # Market making efficiency
    edge_per_btc = 0
    edge_total = 0
    fees_pct_of_edge = 0
    if buy_vwap > 0 and sell_vwap > 0:
        edge_per_btc = sell_vwap - buy_vwap
        edge_total = edge_per_btc * min(buy_vol, sell_vol)
        fees_pct_of_edge = (fees_usd / edge_total * 100) if edge_total > 0 else 0

    roundtrips = min(len(buy_fills), len(sell_fills))

    # PnL distribution
    pnl_values = [p["pnl"] for p in pnl_curve]
    if pnl_values:
        sorted_pnl = sorted(pnl_values)
        n = len(sorted_pnl)
        pnl_distribution = {
            "min": sorted_pnl[0],
            "p25": sorted_pnl[n // 4],
            "median": sorted_pnl[n // 2],
            "p75": sorted_pnl[3 * n // 4],
            "max": sorted_pnl[-1],
            "final": pnl_values[-1],
            "time_in_profit_pct": round(sum(1 for p in pnl_values if p > 0) / n * 100, 1),
        }
    else:
        pnl_distribution = {
            "min": 0, "p25": 0, "median": 0, "p75": 0,
            "max": 0, "final": 0, "time_in_profit_pct": 0,
        }

    # Calmar ratio
    calmar = (total_pnl / max_dd) if (total_pnl > 0 and max_dd > 0) else 0

    return {
        "execution": {
            "replay_time_s": round(elapsed, 1),
        },
        "parameters": {
            "risk_aversion": risk_aversion,
            "order_size": order_size,
            "session_duration": session_duration,
            "liquidity_param": strategy.k,
            "final_sigma": round(strategy.sigma, 4),
        },
        "summary": {
            "total_fills": len(fills),
            "buy_fills": len(buy_fills),
            "sell_fills": len(sell_fills),
            "maker_fills": len(maker_fills),
            "taker_fills": len(taker_fills),
            "buy_volume_btc": round(buy_vol, 8),
            "sell_volume_btc": round(sell_vol, 8),
            "total_volume_btc": round(total_volume_btc, 8),
            "total_volume_usd": round(total_volume_usd, 2),
            "net_volume_btc": round(buy_vol - sell_vol, 8),
            "final_mid_price": round(mid, 2),
            "buy_vwap": round(buy_vwap, 2),
            "sell_vwap": round(sell_vwap, 2),
            "vwap_spread": round(sell_vwap - buy_vwap, 2),
            "fill_price_min": round(min_price, 2),
            "fill_price_max": round(max_price, 2),
            "fill_price_mean": round(mean_price, 2),
            "realised_cash_usd": round(cash_usd, 2),
            "open_position_btc": round(pos_btc, 8),
            "unrealised_pnl_usd": round(unrealised, 2),
            "fees_paid_usd": round(fees_usd, 2),
            "total_pnl_usd": round(total_pnl, 2),
            "pnl_per_fill": round(total_pnl / len(fills), 4) if fills else 0,
            "pnl_per_btc_traded": round(total_pnl / total_volume_btc, 2) if total_volume_btc else 0,
            "max_drawdown_usd": round(max_dd, 2),
            "calmar_ratio": round(calmar, 2),
            "inventory_value_usd": round(abs(unrealised), 2),
            "edge_per_btc": round(edge_per_btc, 2),
            "edge_total": round(edge_total, 2),
            "fees_pct_of_edge": round(fees_pct_of_edge, 1),
            "roundtrips": roundtrips,
            "resting_orders": len(engine.book.orders),
        },
        "pnl_distribution": pnl_distribution,
        "pnl_curve": pnl_curve,
        "inventory_curve": inventory_curve,
        "fill_scatter": fill_scatter,
        "spread_series": spread_series,
    }
