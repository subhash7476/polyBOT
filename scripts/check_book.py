"""Diagnostic: check public CLOB order books for the bot's resting 5sh orders."""
import httpx

# token_id, expected bid price, expected ask price, label
TARGETS = [
    ("910720513355", 0.405, 0.495, "US GDP >2.5%"),
    ("842048437633", 0.220, 0.250, "Spencer Pratt"),
    ("227221403258", 0.290, 0.320, "Oh Se-hoon"),
    ("750287527761", 0.390, 0.420, "Fed rate hike"),
]

with httpx.Client(timeout=15) as c:
    for tid, want_bid, want_ask, label in TARGETS:
        r = c.get(f"https://clob.polymarket.com/book?token_id={tid}")
        if r.status_code != 200:
            print(f"{label}: book fetch {r.status_code}")
            continue
        b = r.json()
        bids = {round(float(x['price']), 4): float(x['size']) for x in b.get('bids', [])}
        asks = {round(float(x['price']), 4): float(x['size']) for x in b.get('asks', [])}
        bid_here = bids.get(round(want_bid, 4), 0.0)
        ask_here = asks.get(round(want_ask, 4), 0.0)
        print(f"{label:16s} bid {want_bid}: {bid_here}sh resting | "
              f"ask {want_ask}: {ask_here}sh resting")
