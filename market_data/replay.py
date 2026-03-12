import time
from typing import Iterator, List
from common.types import MarketEvent
from common.events import NormalizedEvent
from market_data.sorter import sort_market_events
from market_data.normalizer import normalize_event

class ReplayEngine:
    """
    Consumes a stream of dual-timestamped Bitfinex MarketEvents, buffers them, 
    and yields them in a deterministic strictly sorted order using playback controls.
    """
    def __init__(self, buffer_size: int = 1000):
        self.buffer_size = buffer_size
        self._buffer: List[MarketEvent] = []
        self._is_running = False
        self._speed = 1.0

    def start(self):
        """Starts or resumes the replay."""
        self._is_running = True

    def stop(self):
        """Pauses or stops the replay."""
        self._is_running = False

    def set_speed(self, speed: float):
        """Sets playback speed multiplier (1.0 = real-time, 0 = no delay)."""
        self._speed = speed

    def process(self, events: Iterator[MarketEvent]) -> Iterator[MarketEvent]:
        """
        Takes an underlying iterator of raw events (e.g. from reader.py) 
        and yields them in a deterministic sorted order, processing the buffer.
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

    def play(self, events: Iterator[MarketEvent]) -> Iterator[NormalizedEvent]:
        """
        Consumes raw bitfinex events, sorts them, normalizes them into L3 Orders, 
        and yields them with appropriate delays according to local_ts_ns and speed.
        """
        self.start()
        last_ts_ns = None

        for raw_event in self.process(events):
            if not self._is_running:
                break
                
            if last_ts_ns is not None and self._speed > 0:
                delta_ns = raw_event.local_ts_ns - last_ts_ns
                if delta_ns > 0:
                    sleep_sec = (delta_ns / 1_000_000_000.0) / self._speed
                    # Skip extremely small sleeps
                    if sleep_sec > 0.001:
                        time.sleep(sleep_sec)
                        
            last_ts_ns = raw_event.local_ts_ns

            # Normalize and yield Bitfinex L3 OrderUpdates / OrderCancels
            for norm_event in normalize_event(raw_event):
                yield norm_event
