"""
Quick test: connect to Bitfinex WebSocket, enable TIME flag (32),
subscribe to R0 raw book, and print the first few messages to verify
the exact format of the mts field.
"""

import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip install websockets")
    sys.exit(1)

BITFINEX_WS_URL = "wss://api-pub.bitfinex.com/ws/2"
SYMBOL = "tBTCUSD"

CONF_MSG = json.dumps({
    "event": "conf",
    "flags": 131072 + 32768,  # CHECKSUM (131072) + TIMESTAMP (32768) = 163840
})

SUBSCRIBE_MSG = json.dumps({
    "event": "subscribe",
    "channel": "book",
    "prec": "R0",
    "freq": "F0",
    "symbol": SYMBOL,
    "len": "25",  # small snapshot for quick test
})


async def main():
    print(f"Connecting to {BITFINEX_WS_URL}...")
    async with websockets.connect(BITFINEX_WS_URL, ping_interval=20) as ws:
        print("Connected. Sending conf (CHECKSUM + TIME) and subscribe...\n")
        await ws.send(CONF_MSG)
        await ws.send(SUBSCRIBE_MSG)

        count = 0
        max_messages = 15  # enough to see snapshot + a few updates

        async for raw_msg in ws:
            data = json.loads(raw_msg)
            count += 1

            # Dict messages (info, subscribed, conf)
            if isinstance(data, dict):
                print(f"[{count}] DICT: {json.dumps(data, indent=2)}")
                print()
                continue

            # Array messages — this is where we check for mts
            if isinstance(data, list):
                last = data[-1]
                has_mts = isinstance(last, (int, float)) and last > 1e12

                # Determine type
                if len(data) >= 2 and data[1] == "hb":
                    msg_type = "HEARTBEAT"
                elif len(data) >= 2 and isinstance(data[1], list):
                    payload = data[1]
                    if payload and isinstance(payload[0], list):
                        msg_type = f"SNAPSHOT ({len(payload)} orders)"
                    elif len(payload) == 3:
                        msg_type = "UPDATE (nested)"
                    else:
                        msg_type = "ARRAY"
                elif len(data) >= 3 and data[1] == "cs":
                    msg_type = "CHECKSUM"
                elif len(data) >= 4:
                    msg_type = "UPDATE (flat)"
                else:
                    msg_type = "UNKNOWN"

                print(f"[{count}] {msg_type}")
                print(f"  Length: {len(data)} elements")
                print(f"  Last element: {last} (type={type(last).__name__})")
                print(f"  Has mts (>1e12): {has_mts}")
                if has_mts:
                    import datetime
                    ts_sec = last / 1000
                    dt = datetime.datetime.fromtimestamp(ts_sec, tz=datetime.timezone.utc)
                    print(f"  mts decoded: {dt.isoformat()}")

                # For snapshot, show structure of first entry
                if msg_type.startswith("SNAPSHOT"):
                    print(f"  First order: {data[1][0]}")
                    # Show full structure minus the huge order list
                    truncated = [data[0], f"[...{len(data[1])} orders...]"] + data[2:]
                    print(f"  Structure: {truncated}")
                elif msg_type.startswith("UPDATE"):
                    print(f"  Full: {data}")

                print()

            if count >= max_messages:
                break

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
