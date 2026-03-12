import sys
from market_data.reader import read_capture_file
from market_data.replay import ReplayEngine

def main():
    # Use the sample log provided by the team in main
    capture_file = "./tests/fixtures/events_l3_sample.log"
    
    print(f"Reading Bitfinex events from {capture_file}...")
    
    # 1. Read raw events
    raw_event_stream = read_capture_file(capture_file, strict=False)
    
    # 2. Replay & Normalize
    engine = ReplayEngine(buffer_size=10)
    engine.set_speed(0.0) # Play as fast as possible for the demo
    
    normalized_stream = engine.play(raw_event_stream)
    
    # Print what comes out of the normalized stream
    print("--- Bitfinex Normalized Events Emitted ---")
    event_count = 0
    for event in normalized_stream:
        print(f"[{event.exchange_ts_ns or event.local_ts_ns}] Event: {event}")
        event_count += 1
        if event_count > 20: 
            print("... (truncated to 20 lines) ...")
            break
        
    print(f"--- Done. Processed stream! ---")

if __name__ == "__main__":
    main()
