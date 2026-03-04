"""Week 1 capture stub that writes sample lines to a file."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from pathlib import Path

import websockets

from common.time_utils import now_local_ns

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"


def build_line(channel: str, raw_json: str) -> str:
    return f"{now_local_ns()}|{channel}|{raw_json}"


def generate_coinbase_signature(api_secret: str, api_key: str, passphrase: str) -> dict[str, str] | None:
    """Generates the required HMAC signature for Coinbase WebSocket authentication."""
    if not all([api_secret, api_key, passphrase]):
        return None

    timestamp = str(int(time.time()))
    message = timestamp + "GET" + "/users/self/verify"
    
    # Decode the base64 secret, hash it via HMAC SHA256, and encode back to base64
    hmac_key = base64.b64decode(api_secret)
    signature = hmac.new(hmac_key, message.encode("utf-8"), hashlib.sha256)
    signature_b64 = base64.b64encode(signature.digest()).decode("utf-8")

    return {
        "signature": signature_b64,
        "key": api_key,
        "passphrase": passphrase,
        "timestamp": timestamp,
    }


async def file_writer(queue: asyncio.Queue, output_path: Path):
    """Reads from the queue and writes to the output file."""
    logging.info(f"Starting file writer targeting {output_path}")
    
    with output_path.open("a", encoding="utf-8") as file_handle:
        while True:
            try:
                channel, raw_json = await queue.get()
                line = build_line(channel, raw_json)
                
                file_handle.write(line + "\n")
                file_handle.flush()
                
                queue.task_done()
            except asyncio.CancelledError:
                logging.info("Writer task cancelled.")
                break
            except Exception as e:
                logging.error(f"Error in file writer: {e}")


async def websocket_listener(queue: asyncio.Queue, channels: list[str], product_ids: list[str]):
    """Connects to Coinbase and puts messages on the queue."""
    
    subscribe_message = {
        "type": "subscribe",
        "product_ids": product_ids,
        "channels": channels
    }

    # Attempt to load credentials from the environment
    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    api_passphrase = os.getenv("COINBASE_API_PASSPHRASE")

    # If keys exist, generate the auth payload and inject it into the subscription message
    auth_payload = generate_coinbase_signature(api_secret, api_key, api_passphrase)
    if auth_payload:
        logging.info("API Keys detected. Injecting HMAC authentication payload.")
        subscribe_message.update(auth_payload)
    else:
        logging.info("API Keys missing. Connecting anonymously to public channels.")

    while True:
        try:
            logging.info(f"Connecting to {COINBASE_WS_URL}...")
            async with websockets.connect(COINBASE_WS_URL, ping_interval=30, ping_timeout=10) as websocket:
                logging.info(f"Connected. Subscribing to: {channels}")
                await websocket.send(json.dumps(subscribe_message))
                
                while True:
                    message_str = await websocket.recv()
                    
                    try:
                        data = json.loads(message_str)
                        msg_type = data.get("type", "unknown")
                        
                        # Forward only real data
                        if msg_type in ("subscriptions", "error"):
                            logging.info(f"System message: {message_str}")
                            # If connection dies due to bad auth, backoff and retry
                            if msg_type == "error":
                                await asyncio.sleep(5) 
                            continue
                            
                        await queue.put((msg_type, message_str))
                        
                    except json.JSONDecodeError:
                        logging.warning("Failed to decode json message.")
                        
        except websockets.ConnectionClosed as e:
            logging.warning(f"Websocket closed ({e}). Reconnecting in 5s...")
            await asyncio.sleep(5)
        except Exception as e:
            logging.error(f"Unexpected error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)


async def async_main(output_path: Path):
    queue = asyncio.Queue()
    
    has_keys = all([os.getenv("COINBASE_API_KEY"), os.getenv("COINBASE_API_SECRET"), os.getenv("COINBASE_API_PASSPHRASE")])
    
    # Default to public channels if no keys, otherwise we capture the target `level2` orderbook
    target_channels = ["heartbeat", "level2"] if has_keys else ["heartbeat", "ticker"]
    product_ids = ["BTC-USD"]
    
    listener_task = asyncio.create_task(websocket_listener(queue, target_channels, product_ids))
    writer_task = asyncio.create_task(file_writer(queue, output_path))
    
    logging.info("Capture pipeline running. Press Ctrl+C to stop.")
    
    try:
        await asyncio.gather(listener_task, writer_task)
    except asyncio.CancelledError:
        logging.info("Shutting down cleanly...")


def main() -> int:
    output_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("capture_stub_output.log")
    
    try:
        asyncio.run(async_main(output_path))
    except KeyboardInterrupt:
        logging.info("Interrupted by user. Exiting.")
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
