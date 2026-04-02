import os
import json
import shutil
import pytest
import subprocess
from pathlib import Path

@pytest.fixture
def mock_data_dir(tmp_path):
    day_dir = tmp_path / "2024-05-15"
    day_dir.mkdir()
    
    # Create events.log
    # Let's add 1 snapshot event (base) and then some updates.
    # Base timestamp: 2024-05-15 10:00:00 UTC = 1715767200000000000
    base_ns = 1715767200 * 1_000_000_000
    events_file = day_dir / "events.log"
    with open(events_file, "w") as f:
        # Initial Snapshot Event
        f.write(f"{base_ns}|1715767200000000000|bf_snapshot|[1, ["
                "[123, 70000, 1.5], "  # Bid
                "[124, 70100, -2.0]"   # Ask
                "]]\n")
                
        # Some update before 10:10:00 (which is +600s = 1715767800)
        update_ns_1 = base_ns + 100 * 1_000_000_000
        # modify bid to 70050
        f.write(f"{update_ns_1}|{update_ns_1}|bf_update|[1, 222, 70050, 1.0]\n")
        
        # Another update right before next snapshot
        update_ns_2 = base_ns + 200 * 1_000_000_000
        # modify ask to 70080
        f.write(f"{update_ns_2}|{update_ns_2}|bf_update|[1, 333, 70080, -1.0]\n")

        # After the next snapshot, add an event
        update_ns_3 = base_ns + 700 * 1_000_000_000
        f.write(f"{update_ns_3}|{update_ns_3}|bf_update|[1, 444, 70020, 1.0]\n")

    # Snapshot at 10:00:00 (baseline)
    snap1 = day_dir / "snapshot_100000.json"
    with open(snap1, "w") as f:
        json.dump({
            "123": {"price": 70000, "amount": 1.5},
            "124": {"price": 70100, "amount": -2.0}
        }, f)
        
    # Snapshot at 10:10:00 (after two updates -> Best Bid 70050, Best Ask 70080)
    snap2 = day_dir / "snapshot_101000.json"
    with open(snap2, "w") as f:
        json.dump({
            "123": {"price": 70000, "amount": 1.5},
            "124": {"price": 70100, "amount": -2.0},
            "222": {"price": 70050, "amount": 1.0},
            "333": {"price": 70080, "amount": -1.0}
        }, f)

    return day_dir

def test_compare_snapshots_success(mock_data_dir):
    """
    Test that the comparison framework correctly identifies matching books
    """
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "compare_snapshots.py"
    
    import sys
    result = subprocess.run(
        [sys.executable, str(script_path), str(mock_data_dir)],
        capture_output=True,
        text=True
    )
    
    # Since prices match exactly, exit code should be 0
    assert result.returncode == 0, f"Script failed with output:\n{result.stderr}\n{result.stdout}"
    assert "Perfect Matches           : 2" in result.stderr or "Perfect Matches           : 2" in result.stdout

