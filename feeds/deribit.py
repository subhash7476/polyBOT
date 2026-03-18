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
    ]},
}
_SUBSCRIBE_OPTIONS = {
    "jsonrpc": "2.0", "id": 2, "method": "public/subscribe",
    "params": {"channels": ["markprice.options.btc_usd"]},
}


def parse_dvol_message(msg: dict) -> Optional[dict]:
    if msg.get("method") != "subscription":
        return None
    channel = msg.get("params", {}).get("channel", "")
    data = msg.get("params", {}).get("data", {})
    if channel == "deribit_volatility_index.btc_usd":
        return {"btc_dvol": data["volatility"], "btc_price": data["index_price"]}
    if channel == "deribit_volatility_index.eth_usd":
        return {"eth_dvol": data["volatility"], "eth_price": data["index_price"]}
    return None


def _compute_skew(calls: list, puts: list, target_delta: float = 0.25) -> float:
    """25-delta put IV minus 25-delta call IV. Positive = downside bias."""
    if not calls or not puts:
        return 0.0
    call_25 = min(calls, key=lambda x: abs(abs(x["delta"]) - target_delta))
    put_25  = min(puts,  key=lambda x: abs(abs(x["delta"]) - target_delta))
    return put_25["iv"] - call_25["iv"]


def _compute_term_structure(instruments: list) -> float:
    """Front/back vol ratio — placeholder until expiry parsing is wired."""
    return 1.0


def parse_options_chain(instruments: list) -> dict:
    """Parse markprice.options payload → skew + call/put counts."""
    calls, puts = [], []
    for inst in instruments:
        name = inst.get("instrument_name", "")
        iv = inst.get("iv", 0)
        delta = inst.get("delta", 0)
        if not iv:
            continue
        if name.endswith("-C"):
            calls.append({"iv": iv, "delta": delta, "name": name})
        elif name.endswith("-P"):
            puts.append({"iv": iv, "delta": delta, "name": name})

    return {
        "skew": _compute_skew(calls, puts),
        "term_ratio": _compute_term_structure(calls + puts),
        "call_count": len(calls),
        "put_count": len(puts),
    }


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
                        await self._state.update_feeds(**update)
                        self.log.debug(f"dvol update: {update}")

                elif "markprice.options" in channel:
                    parsed = parse_options_chain(msg["params"].get("data", []))
                    await self._state.update_feeds(
                        btc_vol_skew=parsed["skew"],
                        btc_term_ratio=parsed["term_ratio"],
                    )
