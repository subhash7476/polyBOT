import json
import asyncio
import websockets
from typing import Optional
from config import DERIBIT_WS_URL
from feeds.base import BaseFeed
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

_SUBSCRIBE_DVOL = {
    "jsonrpc": "2.0", "id": 1, "method": "public/subscribe",
    "params": {"channels": [
        "deribit_volatility_index.btc_usd",
        "deribit_volatility_index.eth_usd",
        "deribit_volatility_index.sol_usd",
    ]},
}
_SUBSCRIBE_OPTIONS = {
    "jsonrpc": "2.0", "id": 2, "method": "public/subscribe",
    "params": {"channels": ["markprice.options.btc_usd"]},
}


_DVOL_CHANNEL_TO_ASSET = {
    "deribit_volatility_index.btc_usd": "BTC",
    "deribit_volatility_index.eth_usd": "ETH",
    "deribit_volatility_index.sol_usd": "SOL",
}


def parse_dvol_message(msg: dict) -> Optional[dict]:
    """
    Parse Deribit DVOL subscription message.
    Payload: {timestamp, index_name, volatility} — no index_price (confirmed live).
    Returns {asset: symbol, dvol: value} or None if not a DVOL message.
    """
    if msg.get("method") != "subscription":
        return None
    channel = msg.get("params", {}).get("channel", "")
    data = msg.get("params", {}).get("data", {})
    asset = _DVOL_CHANNEL_TO_ASSET.get(channel)
    if asset is None:
        return None
    return {"asset": asset, "dvol": data["volatility"]}


def parse_options_chain(instruments: list) -> dict:
    """
    Parse markprice.options payload → vol skew.
    Deribit sends: {timestamp, iv, instrument_name, mark_price} — no delta field.
    Near-OTM filter: mark_price in [0.005, 0.08] BTC selects options near ATM
    (deep OTM → mark≈0, deep ITM → mark>0.5).
    Skew = median(put IV) - median(call IV) for those options. Positive = downside bias.
    """
    call_ivs, put_ivs = [], []
    for inst in instruments:
        name = inst.get("instrument_name", "")
        iv = inst.get("iv", 0)
        mark = inst.get("mark_price", 0)
        if not iv or iv <= 0 or iv > 5:   # skip zero or wildly illiquid
            continue
        if not (0.005 <= mark <= 0.08):   # near-OTM filter
            continue
        if name.endswith("-C"):
            call_ivs.append(iv)
        elif name.endswith("-P"):
            put_ivs.append(iv)

    if not call_ivs or not put_ivs:
        return {"skew": 0.0, "call_count": len(call_ivs), "put_count": len(put_ivs)}

    call_ivs.sort()
    put_ivs.sort()
    med_call = call_ivs[len(call_ivs) // 2]
    med_put  = put_ivs[len(put_ivs) // 2]
    skew = med_put - med_call

    return {"skew": skew, "call_count": len(call_ivs), "put_count": len(put_ivs)}


class DeribitFeed(BaseFeed):
    def __init__(self, state: AppState):
        super().__init__("deribit")
        self._state = state

    async def _run(self):
        async with websockets.connect(DERIBIT_WS_URL, ping_interval=30) as ws:
            await ws.send(json.dumps(_SUBSCRIBE_DVOL))
            await ws.send(json.dumps(_SUBSCRIBE_OPTIONS))
            self.log.info("subscribed to DVOL + options chain")
            async for raw in ws:
                msg = json.loads(raw)
                if "params" not in msg:
                    continue
                channel = msg["params"].get("channel", "")

                if "deribit_volatility_index" in channel:
                    update = parse_dvol_message(msg)
                    if update:
                        await self._state.update_asset_feed(update["asset"], dvol=update["dvol"])
                        self.log.debug(f"dvol {update['asset']}={update['dvol']:.1f}")

                elif "markprice.options" in channel:
                    parsed = parse_options_chain(msg["params"].get("data", []))
                    if parsed["call_count"] and parsed["put_count"]:
                        await self._state.update_asset_feed("BTC", vol_skew=parsed["skew"])
                        self.log.debug(f"skew={parsed['skew']:.4f} calls={parsed['call_count']} puts={parsed['put_count']}")
