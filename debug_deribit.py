"""Debug Deribit WebSocket — run with: python debug_deribit.py"""
import asyncio
import json
import websockets

WS_URL = "wss://www.deribit.com/ws/api/v2"

SUBSCRIBE = {
    "jsonrpc": "2.0", "id": 1, "method": "public/subscribe",
    "params": {"channels": [
        "deribit_volatility_index.btc_usd",
        "deribit_volatility_index.eth_usd",
        "markprice.options.btc_usd",
    ]},
}


async def main():
    print(f"Connecting to {WS_URL} ...")
    async with websockets.connect(WS_URL, ping_interval=30) as ws:
        await ws.send(json.dumps(SUBSCRIBE))
        print("Subscribe sent. Waiting for messages (Ctrl+C to stop)...\n")

        msg_count = 0
        async for raw in ws:
            msg = json.loads(raw)
            msg_count += 1

            method = msg.get("method", "")
            channel = msg.get("params", {}).get("channel", "")
            data = msg.get("params", {}).get("data", {})

            # Subscription confirmation
            if "result" in msg:
                print(f"[{msg_count}] SUBSCRIPTION ACK: {msg.get('result')}")
                continue

            if method != "subscription":
                print(f"[{msg_count}] OTHER: {json.dumps(msg)[:200]}")
                continue

            # DVOL message
            if "deribit_volatility_index" in channel:
                print(f"[{msg_count}] DVOL | channel={channel}")
                print(f"       keys in data: {list(data.keys())}")
                print(f"       data={json.dumps(data)}")
                print()

            # Options chain message
            elif "markprice.options" in channel:
                instruments = data if isinstance(data, list) else []
                print(f"[{msg_count}] OPTIONS | channel={channel} | instruments={len(instruments)}")
                if instruments:
                    print(f"       First instrument keys: {list(instruments[0].keys())}")
                    print(f"       Sample: {json.dumps(instruments[0])}")
                print()

            else:
                print(f"[{msg_count}] UNKNOWN | channel={channel} | {json.dumps(msg)[:200]}")


asyncio.run(main())
