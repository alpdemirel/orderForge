import sys
import json
from pathlib import Path

def analyze_event_log(filepath: Path):
    print(f"=== Analyzing {filepath.name} ===")
    
    first_ts = None
    last_ts = None
    prev_ts = None
    
    reconnects = []
    ts_jumps = []
    
    total_lines = 0

    with filepath.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
                
            total_lines += 1
            parts = line.split("|", 3)
            if len(parts) < 4:
                continue
                
            try:
                local_ts = int(parts[0])
            except ValueError:
                continue
                
            channel = parts[2]
            raw_json = parts[3]
            
            if first_ts is None:
                first_ts = local_ts
            last_ts = local_ts
            
            # Check timestamp jumps
            if prev_ts is not None:
                diff_ns = local_ts - prev_ts
                if diff_ns > 1_000_000_000: # 1 second in nanoseconds
                    ts_jumps.append((prev_ts, local_ts, diff_ns))
            
            prev_ts = local_ts
            
            # Check reconnects
            if channel == "bf_subscribed":
                reconnects.append(local_ts)

    print(f"Total Events: {total_lines}")
    if first_ts and last_ts:
        print(f"Coverage: {first_ts} to {last_ts}")
        coverage_sec = (last_ts - first_ts) / 1_000_000_000
        print(f"Duration: {coverage_sec:.2f} seconds ({coverage_sec / 3600:.2f} hours)")
    else:
        print("Coverage: N/A (No valid timestamps found)")

    print(f"\nReconnect Events (bf_subscribed): {len(reconnects)}")
    for r in reconnects:
        print(f" - at ns: {r}")

    print(f"\nTimestamp Jumps > 1s: {len(ts_jumps)}")
    # Only print up to 10 jumps so we don't flood the output if there are many
    for j in ts_jumps[:10]:
        print(f" - Jump of {j[2]/1_000_000_000:.3f}s: from {j[0]} to {j[1]}")
    if len(ts_jumps) > 10:
        print(f"   ... and {len(ts_jumps) - 10} more jumps.")

    print("\n")

def main():
    if len(sys.argv) < 2:
        print("Usage: python gap_analysis.py <path_to_events.log>...")
        sys.exit(1)
        
    for arg in sys.argv[1:]:
        analyze_event_log(Path(arg))

if __name__ == "__main__":
    main()
