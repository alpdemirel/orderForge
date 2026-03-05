from dataclasses import dataclass
from typing import Any, Dict

@dataclass
class MarketEvent:
    """
    Standardized event built from a parsed capture line.
    
    Fields:
      - local_ts_ns: Capture local timestamp in nanoseconds
      - channel: Websocket channel name (e.g. 'level2', 'full', 'heartbeat')
      - raw_json: The dictionary representation of the original JSON payload
    """
    local_ts_ns: int
    channel: str
    raw_json: Dict[str, Any]
