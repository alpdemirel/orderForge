from typing import Iterator, List
from common.types import MarketEvent
from market_data.sorter import sort_market_events

class ReplayEngine:
    """
    Consumes a stream of MarketEvents, buffers them, and yields them in a 
    deterministic, strictly sorted order.
    """
    def __init__(self, buffer_size: int = 1000):
        self.buffer_size = buffer_size
        self._buffer: List[MarketEvent] = []

    def process(self, events: Iterator[MarketEvent]) -> Iterator[MarketEvent]:
        """
        Takes an underlying iterator of raw events (e.g. from file reader) 
        and yields them in a deterministic sorted order.
        """
        for event in events:
            self._buffer.append(event)
            
            # When buffer is full, sort it and yield
            if len(self._buffer) >= self.buffer_size:
                sorted_events = sort_market_events(self._buffer)
                for sorted_event in sorted_events:
                    yield sorted_event
                self._buffer.clear()
                
        # Flush remaining boundary
        if self._buffer:
            sorted_events = sort_market_events(self._buffer)
            for sorted_event in sorted_events:
                yield sorted_event
            self._buffer.clear()
