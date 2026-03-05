import sys
from market_data.reader import read_capture_file
from market_data.replay import ReplayEngine

def main():
    capture_file = "./capture_stub_output.log"
    
    print(f"Reading from {capture_file}...")
    
    # 1. Read raw events (Step B1)
    raw_event_stream = read_capture_file(capture_file, strict=False)
    
    # 2. Replay & Normalize (Steps B2, B3, B4)
    engine = ReplayEngine(buffer_size=10)
    engine.set_speed(0.0) # Play as fast as possible for the demo
    
    normalized_stream = engine.play(raw_event_stream)
    
    # Print what comes out of the normalized stream
    print("--- Normalized Events Emitted ---")
    event_count = 0
    for event in normalized_stream:
        print(f"[{event.local_ts_ns}] Event: {event}")
        event_count += 1
        
    print(f"--- Done. Processed {event_count} normalized events ---")

if __name__ == "__main__":
    main()
