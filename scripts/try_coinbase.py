import asyncio
import json
import websockets

URI = "wss://ws-feed.exchange.coinbase.com"

async def test_coinbase_stream():
    # Connect to the WebSocket
    async with websockets.connect(URI) as websocket:
        
        # Build the subscription message
        subscribe_message = {
            "type": "subscribe",
            "product_ids": ["BTC-USD"],
            "channels": ["level2", "heartbeat"]
        }
        
        # Send the subscription message
        await websocket.send(json.dumps(subscribe_message))
        print("Subscribed to Coinbase! Waiting for messages...\n")
        
        # Listen for the first 5 messages just to see what they look like
        for i in range(5):
            message = await websocket.recv()
            # Parse the JSON string into a Python dictionary
            data = json.loads(message)
            
            # Print it out nicely
            print(f"Message {i + 1}:")
            print(json.dumps(data, indent=2))
            print("-" * 40)

if __name__ == "__main__":
    # Run the async function
    asyncio.run(test_coinbase_stream())
