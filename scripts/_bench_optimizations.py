"""
Benchmark: eski json vs orjson (SSE + reader) ve SortedDict vs dict order book.
Gerçek data üzerinde çalışır.
"""
import sys, os, time, json, statistics
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import orjson
from sortedcontainers import SortedDict

EVENTS_FILE = "data/2026-05-07/events.log"
RUNS = 3

# ─── YARDIMCI ────────────────────────────────────────────────────────────────
def hdr(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)

def timing(label, fn, n=1):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        result = fn()
        times.append(time.perf_counter() - t0)
    best = min(times)
    print(f"  {label:<40} {best*1000:>8.1f} ms")
    return result, best

# ─── 1. JSON PARSE: stdlib vs orjson ─────────────────────────────────────────
hdr("1. JSON PARSE  (stdlib json vs orjson)")

with open(EVENTS_FILE, "rb") as f:
    raw_lines = f.readlines()

# Sadece JSON içeren satırları al
json_payloads_bytes = []
json_payloads_str = []
for line in raw_lines:
    parts = line.split(b"|", 3)
    if len(parts) >= 3:
        payload = parts[-1].strip()
        if payload:
            json_payloads_bytes.append(payload)
            json_payloads_str.append(payload.decode())

print(f"  Toplam JSON payload: {len(json_payloads_bytes):,} satır")

_, t_stdlib = timing("stdlib json.loads (str)",
    lambda: [json.loads(p) for p in json_payloads_str], RUNS)

_, t_orjson = timing("orjson.loads (bytes, binary mode)",
    lambda: [orjson.loads(p) for p in json_payloads_bytes], RUNS)

speedup_json = t_stdlib / t_orjson
print(f"\n  → orjson {speedup_json:.1f}x daha hızlı (JSON parse)")

# ─── 2. FILE READ: text mode vs binary mode ───────────────────────────────────
hdr("2. FILE I/O  (text utf-8 vs binary)")

def read_text():
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        return f.readlines()

def read_binary():
    with open(EVENTS_FILE, "rb") as f:
        return f.readlines()

_, t_text = timing("open('r', encoding='utf-8')", read_text, RUNS)
_, t_bin  = timing("open('rb')              ", read_binary, RUNS)
print(f"\n  → binary mode {t_text/t_bin:.2f}x daha hızlı (dosya okuma)")

# ─── 3. SSE SERİALİZASYON: json vs orjson ───────────────────────────────────
hdr("3. SSE SERİALİZASYON  (json.dumps vs orjson.dumps)")

sample_events = [
    {"label": "GLFT", "day": "2026-05-07", "status": "running", "error": ""},
    {"label": "AS Strategy #1", "day": "2026-05-07", "status": "completed", "error": ""},
    {"label": "__system__", "day": "__all__", "status": "all_done", "error": ""},
]
N = 100_000

_, t_json_sse = timing(f"json.dumps  ({N:,}x small dict)",
    lambda: [json.dumps(e) for e in sample_events * (N // 3)], RUNS)

_, t_orjson_sse = timing(f"orjson.dumps ({N:,}x small dict)",
    lambda: [orjson.dumps(e).decode() for e in sample_events * (N // 3)], RUNS)

print(f"\n  → orjson {t_json_sse/t_orjson_sse:.1f}x daha hızlı (SSE serialization)")

# ─── 4. ORDER BOOK: list O(N) vs SortedDict O(log N) ─────────────────────────
hdr("4. ORDER BOOK  (list sort O(N) vs SortedDict O(log N))")

import random
prices = [random.randint(80_000_00, 90_000_00) for _ in range(10_000)]

def bench_list_sort(prices):
    """Eski yaklaşım: liste + her best_bid için sort"""
    book = []
    for p in prices:
        book.append(p)
    # Her tick'te best bid almak = sort
    for _ in range(1000):
        best = max(book)  # O(N) her seferinde
    return best

def bench_sorted_dict(prices):
    """Yeni yaklaşım: SortedDict — O(log N) insert, O(1) best"""
    book = SortedDict()
    for p in prices:
        book[p] = book.get(p, 0) + 1
    for _ in range(1000):
        best = book.keys()[-1]  # O(1)
    return best

_, t_list = timing("list + max()  (O(N) per lookup)", lambda: bench_list_sort(prices), RUNS)
_, t_sd   = timing("SortedDict    (O(log N) insert, O(1) best)", lambda: bench_sorted_dict(prices), RUNS)
print(f"\n  → SortedDict {t_list/t_sd:.1f}x daha hızlı (order book ops)")

# ─── 5. GERÇEK E2E BACKTEST  ────────────────────────────────────────────────
hdr("5. GERÇEK BACKTEST  (reader + engine + strategy)")

from market_data.reader import read_capture_file
from exchange.engine import ExchangeEngine
from exchange.gateway import ExchangeGateway
from exchange.models import PRICE_MULTIPLIER, QUANTITY_MULTIPLIER
from market_data.replayer import Replayer
from sim.latency_queue import LatencyQueue
from sim.notifying_exchange import NotifyingExchange
import strategy
from strategy import registry as strategy_registry

def run_backtest():
    strat_cls = strategy_registry.get("avellaneda_stoikov")["cls"]
    strat = strat_cls(risk_aversion=0.001, order_size=0.01, session_duration=86400.0)
    engine = ExchangeEngine()
    gateway = ExchangeGateway(engine)
    notifying = NotifyingExchange(gateway=gateway, on_fill=strat.on_order_fill)
    strat.connect_exchange(notifying)
    lq = LatencyQueue(submit_fn=notifying.submit_order, cancel_fn=notifying.cancel_order)
    strat.connect_latency_queue(lq)
    replayer = Replayer(EVENTS_FILE, strat, engine=engine, on_fill=notifying.emit_fill, latency_queue=lq)
    replayer.run()
    return len(notifying.emitted_fills)

print("  (tek run — JIT warm-up yok, soğuk başlangıç)")
_, t_e2e = timing(f"E2E backtest  ({EVENTS_FILE})", run_backtest, 1)
print(f"\n  → Tüm backtest: {t_e2e*1000:.0f} ms  ({t_e2e:.2f} s)")

# ─── ÖZET ────────────────────────────────────────────────────────────────────
hdr("ÖZET")
print(f"  JSON parse hızlanması:      {speedup_json:.1f}x")
print(f"  File I/O hızlanması:        {t_text/t_bin:.2f}x")
print(f"  SSE serialization:          {t_json_sse/t_orjson_sse:.1f}x")
print(f"  Order book lookup:          {t_list/t_sd:.1f}x")
print(f"  E2E backtest süresi:        {t_e2e:.2f}s")
print()
