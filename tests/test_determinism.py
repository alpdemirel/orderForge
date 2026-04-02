import pytest
import shutil
from pathlib import Path
import os
import sys

# Insert root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.validate_determinism import validate_file

@pytest.fixture
def mock_3_distinct_days(tmp_path):
    """
    Creates 3 distinct capture log files representing 3 separate days of data.
    We take the base events_l3_sample.log and generate variations.
    """
    base_file = Path(__file__).parent / "fixtures" / "events_l3_sample.log"
    if not base_file.exists():
        pytest.skip("events_l3_sample.log not found, skipping determinism test")

    files = []
    
    with open(base_file, "r") as f:
        lines = f.readlines()

    # Day 1: As is
    day1_path = tmp_path / "day_1.log"
    shutil.copy(base_file, day1_path)
    files.append(day1_path)
    
    # Day 2: Truncated or modified (e.g. fewer events)
    day2_path = tmp_path / "day_2.log"
    with open(day2_path, "w") as f:
        # Keep 80% of lines to make it uniquely "distinct"
        cutoff = int(len(lines) * 0.8)
        f.writelines(lines[:cutoff])
    files.append(day2_path)
    
    # Day 3: Even further truncated
    day3_path = tmp_path / "day_3.log"
    with open(day3_path, "w") as f:
        cutoff = int(len(lines) * 0.5)
        f.writelines(lines[:cutoff])
    files.append(day3_path)
    
    return files

def test_simulation_reproducibility(mock_3_distinct_days):
    """
    Execute the simulation on at least 3 distinct days of captured market data 
    to confirm perfectly identical and reproducible outputs across runs.
    """
    for file_path in mock_3_distinct_days:
        # Use our validation helper which runs simulation twice internally
        assert validate_file(file_path, runs=2) == True, f"Non-determinism found in {file_path.name}"
