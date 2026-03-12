import os
import sys

# Add the project root to the Python path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from market_data.reader import read_capture_file
from market_data.replay import ReplayEngine
from strategy.noop import NoOpStrategy
from exchange import ExchangeGateway

def run_historical_simulation():
    capture_file = "./tests/fixtures/events_l3_sample.log"
    print(f"[sim] Initializing exchange gateway...")
    gateway = ExchangeGateway()

    print(f"[sim] Initializing strategy...")
    strategy = NoOpStrategy()
    strategy.connect_exchange(gateway)

    print(f"[sim] Loading historical market data from {capture_file}...")
    # 1. Start reading the raw captured events
    raw_event_stream = read_capture_file(capture_file, strict=False)
    
    # 2. Setup the replay engine buffer
    replay_engine = ReplayEngine(buffer_size=10)
    replay_engine.set_speed(0.0)  # fast-forward replay 
    
    # 3. Pull deterministic normalized events
    normalized_stream = replay_engine.play(raw_event_stream)
    
    processed_count = 0
    print("[sim] Starting discrete-event simulation orchestrator loop...\n")
    
    for event in normalized_stream:
        exch_ts = event.exchange_ts_ns
        local_ts = event.local_ts_ns
        
        # In a real environment, exchange doesn't send 0 timestamps for valid L3 messages. 
        # Fall back to local if missing in capturing artifacts.
        target_ts = exch_ts if exch_ts > 0 else local_ts

        # 4. Route market data into the MIN-HEAP event queue
        gateway.submit_market_update(event, strategy.on_market_data)
        
        # 5. Advance the global exchange simulation clock to the event's timestamp
        # This will pop any resting events (e.g Strategy Orders) and execute them!
        gateway._engine.tick(target_ts)
        
        # 6. Update strategy latency calculations
        strategy.update_clock(exch_ts=exch_ts, local_ts=local_ts)
        
        processed_count += 1
        
        # Optional: Demo a strategy submitting an order occasionally
        if processed_count == 5:
            print(f"[{target_ts}] Strategy reacting to event! Submitting BUY order...")
            # Latency math is injected here. Order is deposited in the heap to be executed in the future.
            strategy.submit_order(price=70_000, side="buy", amount=1, order_type="GTC")
            
    # Flush any remaining items in the queue gracefully
    final_time = gateway._engine.current_time_ns + 1_000_000_000 # advance 1 second
    print(f"[{final_time}] Stream ended. Flushing remaining events from order queue...")
    gateway._engine.tick(final_time)
            
    print(f"\n[sim] Simulation complete. Processed {processed_count} events.")
    print("--- INTERNAL EXCHANGE STATE (ORDER BOOK CHECK) ---")
    print(f"Resting orders: {gateway._engine.book.orders}")

if __name__ == "__main__":
    run_historical_simulation()
