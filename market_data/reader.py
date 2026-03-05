import json
import logging
from typing import Iterator, Optional
from common.types import MarketEvent
from market_data.exceptions import MalformedRecordError

logger = logging.getLogger(__name__)

def parse_line(line: str, strict: bool = False) -> Optional[MarketEvent]:
    """
    Parses a capture line with format: <local_ts_ns>|<channel>|<raw_json>
    """
    line = line.strip()
    if not line:
        return None

    try:
        parts = line.split("|", 2)
        if len(parts) != 3:
            raise ValueError(f"Expected 3 parts, got {len(parts)}. Line: {line}")
            
        local_ts_ns = int(parts[0])
        channel = parts[1]
        raw_json_str = parts[2]
        
        raw_json = json.loads(raw_json_str)
        
        return MarketEvent(
            local_ts_ns=local_ts_ns,
            channel=channel,
            raw_json=raw_json
        )
    except Exception as e:
        if strict:
            raise MalformedRecordError(f"Failed to parse line: {e}") from e
        else:
            logger.warning(f"Skipping malformed line: {e}")
            return None


def read_capture_file(filepath: str, strict: bool = False) -> Iterator[MarketEvent]:
    """
    Reads a capture output file and yields parsed MarketEvents.
    """
    with open(filepath, 'r') as f:
        for line in f:
            event = parse_line(line, strict=strict)
            if event is not None:
                yield event
