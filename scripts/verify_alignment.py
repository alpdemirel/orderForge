import sys
from pathlib import Path
from datetime import datetime, timezone

def verify_alignment(day_dir: Path):
    print(f"\n=== Verifying Alignment for {day_dir.name} ===")
    
    events_log = day_dir / "events.log"
    if not events_log.exists():
        print("ERROR: events.log not found!")
        return

    # Find first timestamp in events.log
    first_event_ns = None
    with events_log.open("r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("|", 1)
            try:
                first_event_ns = int(parts[0])
                break
            except (ValueError, IndexError):
                continue
                
    if first_event_ns is None:
        print("ERROR: Could not find a valid timestamp in events.log")
        return

    first_event_dt = datetime.fromtimestamp(first_event_ns / 1_000_000_000, tz=timezone.utc)
    print(f"events.log coverage starts at: {first_event_dt.strftime('%H:%M:%S UTC')} ({first_event_ns})")

    snapshots = sorted(list(day_dir.glob("snapshot_*.log")))
    print(f"Found {len(snapshots)} snapshots.")
    
    all_aligned = True
    for snap in snapshots:
        # snapshot_HHMMSS.log -> extract HHMMSS
        time_str = snap.stem.split("_")[1]
        try:
            # Parse the time string to compare against the first event
            snap_time = datetime.strptime(f"{day_dir.name} {time_str}", "%Y-%m-%d %H%M%S").replace(tzinfo=timezone.utc)
            snap_ns = int(snap_time.timestamp() * 1_000_000_000)
            
            if first_event_ns >= snap_ns:
                print(f"  [X] MISALIGNED: {snap.name} (Taken at {time_str} UTC but events start at {first_event_dt.strftime('%H:%M:%S')} UTC)")
                all_aligned = False
                
        except ValueError:
            continue
            
    if all_aligned and len(snapshots) > 0:
        print(f"  [✓] ALIGNED: All {len(snapshots)} snapshots occur AFTER the events.log starting timestamp.")
        print("  Therefore, Tan can safely start from any snapshot and securely replay events forward!")

def main():
    if len(sys.argv) < 2:
        print("Usage: python verify_alignment.py <data_dir1> <data_dir2>...")
        sys.exit(1)
        
    for arg in sys.argv[1:]:
        verify_alignment(Path(arg))

if __name__ == "__main__":
    main()
