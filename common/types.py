from dataclasses import dataclass
from typing import Any, Dict, List, Union

@dataclass
class MarketEvent:
    """
    Standardized event built from a parsed capture line.

    Dual-timestamp model (EC2 colocated with exchange):
      - exchange_ts_ns: Timestamp stamped by the exchange at event time (0 if not provided)
      - local_ts_ns: Timestamp stamped by us at capture time

    Fields:
      - local_ts_ns: Capture local timestamp in nanoseconds (when we received the message)
      - exchange_ts_ns: Exchange-side timestamp in nanoseconds (0 when exchange does not provide)
      - channel: Websocket channel name (e.g. 'bf_snapshot', 'bf_update', 'heartbeat')
      - raw_json: The parsed original JSON payload (list or dict)
    """
    local_ts_ns: int
    channel: str
    raw_json: Union[Dict[str, Any], List[Any]]
    exchange_ts_ns: int = 0
