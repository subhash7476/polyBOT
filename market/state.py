import asyncio
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FeedState:
    """All feed-derived values. Feeds write here; engine reads here."""
    # Deribit
    btc_dvol: Optional[float] = None
    eth_dvol: Optional[float] = None
    btc_price: Optional[float] = None
    eth_price: Optional[float] = None
    btc_vol_skew: Optional[float] = None        # 25-delta put IV - call IV
    btc_term_ratio: Optional[float] = None      # front/back vol ratio
    btc_iv_rv_spread: Optional[float] = None    # DVOL - 20d realised vol

    # Microstructure (Binance perp)
    btc_funding_rate: Optional[float] = None
    btc_open_interest: Optional[float] = None

    # On-chain (Glassnode)
    btc_exchange_netflow: Optional[float] = None  # normalised [0,1]
    btc_sopr: Optional[float] = None
    btc_nupl: Optional[float] = None
    btc_whale_ratio: Optional[float] = None
    stablecoin_supply_ratio: Optional[float] = None

    # Macro (Yahoo Finance + FedWatch)
    dxy: Optional[float] = None
    dxy_confidence: float = 0.0         # v2.1 Fix 5: 0=unknown, 1=fresh
    dxy_trend: Optional[float] = None   # (dxy - ma20) / ma20
    yield_2y: Optional[float] = None
    yield_10y: Optional[float] = None
    yield_10y_confidence: float = 0.0   # v2.1 Fix 5
    fed_may_cut_prob: Optional[float] = None
    fed_confidence: float = 0.0         # v2.1 Fix 5


@dataclass
class ContractState:
    """Live CLOB state for a single Polymarket market."""
    yes_token_id: str
    no_token_id: str            # v2.1 Fix 1: store both token IDs
    question: str
    category: str
    best_bid: float = 0.0       # YES bid
    best_ask: float = 1.0       # YES ask
    volume_usd: float = 0.0

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    @property
    def no_best_ask(self) -> float:
        return 1 - self.best_bid  # NO ask = 1 - YES bid


class AppState:
    """Single shared mutable state. All mutations go through async methods."""

    def __init__(self):
        self.feeds = FeedState()
        self.markets: dict[str, ContractState] = {}  # keyed by yes_token_id
        self._lock = asyncio.Lock()

    async def update_feeds(self, **kwargs):
        async with self._lock:
            for k, v in kwargs.items():
                setattr(self.feeds, k, v)

    async def upsert_market(self, state: ContractState):
        async with self._lock:
            self.markets[state.yes_token_id] = state

    async def remove_market(self, yes_token_id: str):
        async with self._lock:
            self.markets.pop(yes_token_id, None)
