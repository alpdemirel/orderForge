"""
Deep fill-accuracy diagnostics — analyses the realistic-ness of simulated
fills, not just book reconstruction.

1. Quote-vs-latency ratio: how often do quotes survive to the book?
2. Adverse selection: does the mid move against us after fills?
3. Fill timing: are fills clustering or evenly distributed?
4. Cancel-fill race: how often do orders get filled during cancel latency?
5. Latency distribution: how variable is observed latency?
"""

import math
import sys
import os
import time
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from exchange.engine import ExchangeEngine
from exchange.gateway import ExchangeGateway
from exchange.models import PRICE_MULTIPLIER, QUANTITY_MULTIPLIER, Side, MARKET_OWNER_ID
from market_data.replayer import Replayer
from market_data.reader import read_capture_file
from sim.latency_queue import LatencyQueue
from sim.notifying_exchange import NotifyingExchange
from strategy.avellaneda_stoikov import AvellanedaStoikovStrategy

import logging
logging.basicConfig(level=logging.WARNING)

EVENTS_FILE = sys.argv[1] if len(sys.argv) > 1 else "data/2026-03-29/events.log"


class InstrumentedStrategy(AvellanedaStoikovStrategy):
    """AS strategy with fill-accuracy instrumentation.

    Performance notes:
    - _pending_adverse is a deque ordered by resolve_tick; popleft() is O(1)
      instead of the old O(N) reversed-list-pop pattern.
    - mid_history replaced by _last_mid/_last_ts (no unbounded list growth).
    - tick_intervals still accumulated for latency stats; bounded by event count.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fill_records = []           # resolved adverse-selection records
        self.quote_count = 0
        self.quote_survived_count = 0
        self.latencies_observed = []
        self.tick_intervals = []
        self.cancel_fail_count = 0

        # Internal state — not public
        self._last_mid: float = 0.0
        self._last_ts: int = 0
        self._tick_count: int = 0
        # deque of (fill_event, fill_mid, fill_ts, side, resolve_at_tick)
        # ordered by insertion == ordered by resolve_at_tick, so popleft is O(1)
        self._pending_adverse: deque = deque()

    def on_market_data(self, event):
        mid = (event["best_bid"] + event["best_ask"]) / 2.0
        ts = event["local_ts_ms"]

        # Tick interval — only need last ts, not full history
        if self._last_ts:
            self.tick_intervals.append(ts - self._last_ts)
        self._tick_count += 1
        self._last_ts = ts
        self._last_mid = mid

        # Resolve adverse-selection fills whose 50-tick window has passed.
        # deque is ordered by insertion, so resolve_at_tick is non-decreasing —
        # we popleft() until we hit a fill not yet ready. O(resolved) total.
        while self._pending_adverse:
            fill_event, fill_mid, fill_ts, side, resolve_at = self._pending_adverse[0]
            if self._tick_count < resolve_at:
                break
            self._pending_adverse.popleft()
            self.fill_records.append({
                "fill_mid": fill_mid,
                "mid_after": mid,
                "side": side,
                "fill_ts": fill_ts,
                "after_ts": ts,
                "price": fill_event.price / PRICE_MULTIPLIER,
            })

        # Track latency
        if self.current_latency_estimate > 0:
            self.latencies_observed.append(self.current_latency_estimate)

        # Quote survival: count orders that are still live on the exchange
        if self._latency_queue is not None:
            get = self.exchange.get_order
            for oid in self._active_order_ids:
                if get(oid) is not None:
                    self.quote_survived_count += 1

        # Parent does the actual quoting (cancel-replace cycle)
        super().on_market_data(event)
        self.quote_count += 1

    def on_order_fill(self, fill_event):
        super().on_order_fill(fill_event)

        fill_mid = self._last_mid or fill_event.price / PRICE_MULTIPLIER
        fill_ts = self._last_ts

        if fill_event.maker_owner_id == "default":
            side = fill_event.maker_side
        elif fill_event.taker_owner_id == "default":
            side = fill_event.taker_side
        else:
            return

        # Schedule resolution 50 ticks from now
        resolve_at = self._tick_count + 50
        self._pending_adverse.append((fill_event, fill_mid, fill_ts, side, resolve_at))


def run():
    strategy = InstrumentedStrategy(
        risk_aversion=0.001,
        order_size=0.01,
        session_duration=86400.0,
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
        EVENTS_FILE, strategy, engine=engine,
        on_fill=notifying.emit_fill,
        latency_queue=latency_queue,
    )

    print(f"Running instrumented backtest on {EVENTS_FILE}...")
    t0 = time.time()
    replayer.run()
    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s")

    fills = notifying.emitted_fills

    # ═══════════════════════════════════════════════════════════════════════
    # 1. LATENCY DISTRIBUTION
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("  1. LATENCY DISTRIBUTION")
    print("=" * 70)

    lats = strategy.latencies_observed
    if lats:
        lats_sorted = sorted(lats)
        n = len(lats_sorted)
        print(f"  Samples:    {n:,}")
        print(f"  Min:        {lats_sorted[0]} ms")
        print(f"  P10:        {lats_sorted[n // 10]} ms")
        print(f"  P25:        {lats_sorted[n // 4]} ms")
        print(f"  Median:     {lats_sorted[n // 2]} ms")
        print(f"  P75:        {lats_sorted[3 * n // 4]} ms")
        print(f"  P90:        {lats_sorted[9 * n // 10]} ms")
        print(f"  P99:        {lats_sorted[int(n * 0.99)]} ms")
        print(f"  Max:        {lats_sorted[-1]} ms")
        mean_lat = sum(lats) / n
        var_lat = sum((l - mean_lat) ** 2 for l in lats) / n
        std_lat = math.sqrt(var_lat)
        print(f"  Mean:       {mean_lat:.1f} ms")
        print(f"  Std dev:    {std_lat:.1f} ms")
        print(f"  CV (std/mean): {std_lat / mean_lat:.2f}")

        # Is it variable enough to matter?
        if std_lat / mean_lat > 0.3:
            print(f"\n  ⚠ HIGH VARIABILITY — point estimate misses {std_lat:.0f}ms of spread")
            print(f"    Impact: orders land ±{std_lat:.0f}ms from predicted time")
            print(f"    Fix: sample from observed distribution instead of point estimate")
        else:
            print(f"\n  Latency is stable — point estimate is adequate")

    # ═══════════════════════════════════════════════════════════════════════
    # 2. QUOTE SURVIVAL RATE
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("  2. QUOTE SURVIVAL (do quotes reach the book?)")
    print("=" * 70)

    print(f"  Total quote cycles:      {strategy.quote_count}")
    print(f"  Quotes on book at check: {strategy.quote_survived_count}")
    # Each cycle places 2 orders (bid + ask)
    total_orders = strategy.quote_count * 2
    survival_rate = strategy.quote_survived_count / total_orders * 100 if total_orders else 0
    print(f"  Survival rate:           {survival_rate:.1f}%")

    if strategy.tick_intervals:
        ti = strategy.tick_intervals
        avg_interval = sum(ti) / len(ti)
        median_interval = sorted(ti)[len(ti) // 2]
        print(f"\n  Tick interval — mean: {avg_interval:.1f}ms, median: {median_interval}ms")
        if lats:
            print(f"  Latency / tick interval: {mean_lat / avg_interval:.2f}x")
            if mean_lat > avg_interval:
                print(f"  ⚠ Latency > tick interval — most quotes are cancelled locally before landing")
            else:
                print(f"  Quotes typically survive {avg_interval / mean_lat:.1f} ticks before replacement")

    # ═══════════════════════════════════════════════════════════════════════
    # 3. ADVERSE SELECTION
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("  3. ADVERSE SELECTION (does mid move against us after fills?)")
    print("=" * 70)

    records = strategy.fill_records
    if records:
        buy_adverse = []
        sell_adverse = []
        for r in records:
            mid_change = r["mid_after"] - r["fill_mid"]
            if r["side"] == Side.BUY:
                # Bought — adverse if mid dropped
                buy_adverse.append(mid_change)
            else:
                # Sold — adverse if mid rose
                sell_adverse.append(-mid_change)  # negative = adverse

        all_adverse = buy_adverse + sell_adverse

        print(f"  Fills analysed: {len(records)} (measured 50 ticks after fill)")
        if buy_adverse:
            avg_buy = sum(buy_adverse) / len(buy_adverse)
            pct_buy_neg = sum(1 for a in buy_adverse if a < 0) / len(buy_adverse) * 100
            print(f"\n  BUY fills ({len(buy_adverse)}):")
            print(f"    Avg mid change after buy:  ${avg_buy:+.4f}")
            print(f"    % where mid dropped:       {pct_buy_neg:.1f}%")
        if sell_adverse:
            avg_sell = sum(sell_adverse) / len(sell_adverse)
            pct_sell_neg = sum(1 for a in sell_adverse if a < 0) / len(sell_adverse) * 100
            print(f"\n  SELL fills ({len(sell_adverse)}):")
            print(f"    Avg mid change after sell: ${avg_sell:+.4f} (negative = moved against)")
            print(f"    % where mid rose:          {pct_sell_neg:.1f}%")

        if all_adverse:
            avg_all = sum(all_adverse) / len(all_adverse)
            pct_adverse = sum(1 for a in all_adverse if a < 0) / len(all_adverse) * 100
            print(f"\n  OVERALL:")
            print(f"    Avg post-fill mid move:    ${avg_all:+.4f}")
            print(f"    % adversely selected:      {pct_adverse:.1f}%")
            if pct_adverse > 55:
                print(f"    ⚠ Significant adverse selection detected")
                print(f"      This means fills tend to happen right before the price moves against us.")
                print(f"      Fix: model fill probability as decreasing when aggressor has info advantage")
            else:
                print(f"    Adverse selection within normal range")

    # ═══════════════════════════════════════════════════════════════════════
    # 4. FILL TIMING DISTRIBUTION
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("  4. FILL TIMING & CLUSTERING")
    print("=" * 70)

    if len(fills) >= 2:
        fill_times = []
        for f in fills:
            # Use fill price as proxy for timing (fills at same price = clustered)
            fill_times.append(f.price / PRICE_MULTIPLIER)

        # Inter-fill intervals (using mid_history timestamps)
        fill_ts_list = []
        for r in strategy.fill_records:
            fill_ts_list.append(r["fill_ts"])

        if len(fill_ts_list) >= 2:
            intervals = [fill_ts_list[i+1] - fill_ts_list[i]
                         for i in range(len(fill_ts_list) - 1) if fill_ts_list[i+1] > fill_ts_list[i]]
            if intervals:
                intervals_sorted = sorted(intervals)
                n = len(intervals_sorted)
                print(f"  Inter-fill intervals ({n} pairs):")
                print(f"    Min:    {intervals_sorted[0]:,.0f} ms")
                print(f"    P10:    {intervals_sorted[n // 10]:,.0f} ms")
                print(f"    Median: {intervals_sorted[n // 2]:,.0f} ms")
                print(f"    P90:    {intervals_sorted[9 * n // 10]:,.0f} ms")
                print(f"    Max:    {intervals_sorted[-1]:,.0f} ms")

                # Check for burst clustering
                burst_threshold = intervals_sorted[n // 10]  # P10
                bursts = sum(1 for i in intervals if i <= burst_threshold)
                print(f"\n    Burst fills (<{burst_threshold}ms apart): {bursts} ({bursts/n:.1%})")

        # Maker vs taker ratio
        maker = sum(1 for f in fills if f.maker_owner_id == "default")
        taker = sum(1 for f in fills if f.taker_owner_id == "default")
        print(f"\n  Maker fills: {maker} ({maker/len(fills):.1%})")
        print(f"  Taker fills: {taker} ({taker/len(fills):.1%})")
        if taker == 0:
            print(f"  ⚠ 0% taker fills — strategy never crosses the spread")
            print(f"    In production, stale quotes landing after price moves would sometimes cross")
            print(f"    This suggests latency is low relative to price movement")

    # ═══════════════════════════════════════════════════════════════════════
    # 5. STALE QUOTE ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("  5. QUOTE STALENESS (how far is fill price from mid at fill time?)")
    print("=" * 70)

    if strategy.fill_records:
        deviations = []
        for r in strategy.fill_records:
            dev = abs(r["price"] - r["fill_mid"])
            deviations.append(dev)

        deviations_sorted = sorted(deviations)
        n = len(deviations_sorted)
        print(f"  Fill-to-mid deviation ({n} fills):")
        print(f"    Min:    ${deviations_sorted[0]:.2f}")
        print(f"    P25:    ${deviations_sorted[n // 4]:.2f}")
        print(f"    Median: ${deviations_sorted[n // 2]:.2f}")
        print(f"    P75:    ${deviations_sorted[3 * n // 4]:.2f}")
        print(f"    Max:    ${deviations_sorted[-1]:.2f}")
        avg_dev = sum(deviations) / n
        print(f"    Mean:   ${avg_dev:.2f}")

    # ═══════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("  ACCURACY IMPROVEMENT OPPORTUNITIES")
    print("=" * 70)


def run_accuracy_analysis(events_file: str) -> dict:
    """Run the same fill-accuracy diagnostics as run() but return a structured
    dict instead of printing.  Safe to call from the API server."""

    strategy = InstrumentedStrategy(
        risk_aversion=0.001,
        order_size=0.01,
        session_duration=86400.0,
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
        events_file, strategy, engine=engine,
        on_fill=notifying.emit_fill,
        latency_queue=latency_queue,
    )

    replayer.run()
    fills = notifying.emitted_fills

    result: dict = {}

    # ── 1. LATENCY ────────────────────────────────────────────────────────
    lats = strategy.latencies_observed
    if lats:
        lats_s = sorted(lats)
        n = len(lats_s)
        mean_lat = sum(lats) / n
        var_lat = sum((l - mean_lat) ** 2 for l in lats) / n
        std_lat = math.sqrt(var_lat)
        result["latency"] = {
            "samples": n,
            "min_ms": lats_s[0],
            "p10_ms": lats_s[n // 10],
            "p25_ms": lats_s[n // 4],
            "median_ms": lats_s[n // 2],
            "p75_ms": lats_s[3 * n // 4],
            "p90_ms": lats_s[9 * n // 10],
            "p99_ms": lats_s[int(n * 0.99)],
            "max_ms": lats_s[-1],
            "mean_ms": round(mean_lat, 1),
            "std_ms": round(std_lat, 1),
            "cv": round(std_lat / mean_lat, 2) if mean_lat else 0,
            "high_variability": (std_lat / mean_lat > 0.3) if mean_lat else False,
        }
    else:
        result["latency"] = None

    # ── 2. QUOTE SURVIVAL ─────────────────────────────────────────────────
    total_orders = strategy.quote_count * 2
    survival_rate = (strategy.quote_survived_count / total_orders * 100) if total_orders else 0
    latency_over_tick = False
    avg_interval = 0.0
    if strategy.tick_intervals:
        ti = strategy.tick_intervals
        avg_interval = sum(ti) / len(ti)
        if lats:
            mean_lat_val = result["latency"]["mean_ms"] if result["latency"] else 0
            latency_over_tick = mean_lat_val > avg_interval
    result["quote_survival"] = {
        "total_cycles": strategy.quote_count,
        "survived": strategy.quote_survived_count,
        "rate_pct": round(survival_rate, 1),
        "avg_tick_interval_ms": round(avg_interval, 1),
        "latency_over_tick": latency_over_tick,
    }

    # ── 3. ADVERSE SELECTION ──────────────────────────────────────────────
    records = strategy.fill_records
    if records:
        buy_adv, sell_adv = [], []
        for r in records:
            mid_change = r["mid_after"] - r["fill_mid"]
            if r["side"] == Side.BUY:
                buy_adv.append(mid_change)
            else:
                sell_adv.append(-mid_change)
        all_adv = buy_adv + sell_adv
        avg_all = sum(all_adv) / len(all_adv) if all_adv else 0
        pct_adverse = (sum(1 for a in all_adv if a < 0) / len(all_adv) * 100) if all_adv else 0

        result["adverse_selection"] = {
            "fills_analysed": len(records),
            "buy_fills": len(buy_adv),
            "sell_fills": len(sell_adv),
            "avg_mid_move_usd": round(avg_all, 4),
            "pct_adversely_selected": round(pct_adverse, 1),
            "avg_buy_mid_change": round(sum(buy_adv) / len(buy_adv), 4) if buy_adv else 0,
            "pct_buy_dropped": round(sum(1 for a in buy_adv if a < 0) / len(buy_adv) * 100, 1) if buy_adv else 0,
            "avg_sell_mid_change": round(sum(sell_adv) / len(sell_adv), 4) if sell_adv else 0,
            "pct_sell_rose": round(sum(1 for a in sell_adv if a < 0) / len(sell_adv) * 100, 1) if sell_adv else 0,
        }
    else:
        result["adverse_selection"] = None

    # ── 4. FILL TIMING ────────────────────────────────────────────────────
    fill_ts_list = [r["fill_ts"] for r in records]
    timing: dict = {}
    if len(fill_ts_list) >= 2:
        intervals = [fill_ts_list[i+1] - fill_ts_list[i]
                     for i in range(len(fill_ts_list) - 1)
                     if fill_ts_list[i+1] > fill_ts_list[i]]
        if intervals:
            isorted = sorted(intervals)
            n = len(isorted)
            burst_threshold = isorted[n // 10]
            bursts = sum(1 for i in intervals if i <= burst_threshold)
            timing["interval_min_ms"] = isorted[0]
            timing["interval_p10_ms"] = isorted[n // 10]
            timing["interval_median_ms"] = isorted[n // 2]
            timing["interval_p90_ms"] = isorted[9 * n // 10]
            timing["interval_max_ms"] = isorted[-1]
            timing["burst_threshold_ms"] = burst_threshold
            timing["burst_count"] = bursts
            timing["burst_pct"] = round(bursts / n * 100, 1)

    if fills:
        maker = sum(1 for f in fills if f.maker_owner_id == "default")
        taker = len(fills) - maker
        timing["total_fills"] = len(fills)
        timing["maker_fills"] = maker
        timing["taker_fills"] = taker
        timing["maker_pct"] = round(maker / len(fills) * 100, 1)
        timing["taker_pct"] = round(taker / len(fills) * 100, 1)

    result["fill_timing"] = timing if timing else None

    # ── 5. QUOTE STALENESS ────────────────────────────────────────────────
    if records:
        devs = sorted(abs(r["price"] - r["fill_mid"]) for r in records)
        n = len(devs)
        result["quote_staleness"] = {
            "fills": n,
            "min_usd": round(devs[0], 2),
            "p25_usd": round(devs[n // 4], 2),
            "median_usd": round(devs[n // 2], 2),
            "p75_usd": round(devs[3 * n // 4], 2),
            "max_usd": round(devs[-1], 2),
            "mean_usd": round(sum(devs) / n, 2),
        }
    else:
        result["quote_staleness"] = None

    return result


if __name__ == "__main__":
    run()
