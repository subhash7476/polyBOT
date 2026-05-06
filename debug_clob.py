"""One-off diagnostic — run with: python debug_clob.py"""
import asyncio
import httpx
import json
from engine.contract_parser import parse_contract

GAMMA_URL = "https://gamma-api.polymarket.com"


async def main():
    async with httpx.AsyncClient(timeout=20.0) as client:
        # Fetch active markets, all tags, paginate until we find parseable ones
        found = []
        offset = 0
        limit = 100
        pages = 0

        while pages < 20 and len(found) < 10:
            resp = await client.get(f"{GAMMA_URL}/markets", params={
                "active": "true", "closed": "false",
                "enableOrderBook": "true",
                "limit": limit, "offset": offset,
            })
            gmarkets = resp.json()
            if not isinstance(gmarkets, list):
                gmarkets = gmarkets.get("data", [])
            if not gmarkets:
                break

            print(f"Page {pages+1}: {len(gmarkets)} markets")
            for m in gmarkets:
                token_ids_raw = m.get("clobTokenIds", "[]")
                try:
                    token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
                except Exception:
                    continue
                if len(token_ids) < 2:
                    continue
                yes_id, no_id = token_ids[0], token_ids[1]
                q = m.get("question", "")
                parsed = parse_contract(yes_id, q)
                if parsed.parseable:
                    found.append({
                        "question": q,
                        "category": parsed.category,
                        "yes_id": yes_id,
                        "no_id": no_id,
                        "volume": m.get("volumeClob", m.get("volume", 0)),
                        "bestBid": m.get("bestBid"),
                        "bestAsk": m.get("bestAsk"),
                        "outcomes": m.get("outcomes"),
                        "outcomePrices": m.get("outcomePrices"),
                        "accepting": m.get("acceptingOrders"),
                    })

            offset += limit
            pages += 1

        print(f"\n=== Found {len(found)} parseable crypto price markets ===")
        for p in found:
            print(f"\n  Q: {p['question']}")
            print(f"  category={p['category']}  volume={p['volume']}  bid={p['bestBid']}  ask={p['bestAsk']}")
            print(f"  outcomes={p['outcomes']}  prices={p['outcomePrices']}")
            print(f"  yes={p['yes_id'][:16]}...  no={p['no_id'][:16]}...")
            print(f"  accepting_orders={p['accepting']}")


asyncio.run(main())
