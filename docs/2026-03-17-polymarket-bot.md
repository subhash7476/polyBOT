# Polymarket Bot Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully automated Python bot that derives probability estimates from Deribit IV, CME FedWatch, and on-chain data, identifies positive-EV Polymarket crypto/finance markets, and executes orders via the Polymarket CLOB API with Kelly sizing and hard risk limits.

**Architecture:** Async Python — independent feed coroutines publish to a shared in-memory state store; the Bayesian engine consumes feed updates to produce model probabilities; the EV gate + Kelly + risk layers decide whether and how large to trade; a single executor places orders; every fill is logged to JSONL for calibration.

**Tech Stack:** Python 3.11+, `asyncio`, `websockets`, `httpx` (async), `numpy`, `py-clob-client`, `eth-account`, `python-dotenv`, `pytest`, `pytest-asyncio`

---

## File Structure

```
polymarket-bot/               ← project root (separate repo)
├── main.py                   ← entry point — wires all coroutines
├── config.py                 ← ALL constants, no logic
├── .env.template             ← key names only (committed)
├── .env                      ← actual secrets (gitignored)
├── requirements.txt
│
├── feeds/
│   ├── __init__.py
│   ├── base.py               ← abstract BaseFeed (start/stop, reconnect)
│   ├── deribit.py            ← DVOL WebSocket → FeedState update
│   ├── fedwatch.py           ← CME FedWatch REST poll → FeedState update
│   └── onchain.py            ← Glassnode REST poll → FeedState update
│
├── engine/
│   ├── __init__.py
│   ├── bayesian.py           ← log-space BayesianEngine class
│   ├── probability.py        ← FeedState → contract model probability
│   └── kl_scanner.py         ← correlated-pair KL divergence detector
│
├── market/
│   ├── __init__.py
│   ├── state.py              ← shared in-memory FeedState + MarketState
│   ├── clob_monitor.py       ← Polymarket WS → MarketState update
│   └── contract_filter.py    ← filter open markets to crypto/finance
│
├── trading/
│   ├── __init__.py
│   ├── ev_gate.py            ← EV calculation; should_enter()
│   ├── kelly.py              ← fractional Kelly sizing
│   ├── risk.py               ← RiskManager — daily loss, position limits
│   └── executor.py           ← CLOBExecutor — place/cancel orders
│
├── tracking/
│   ├── __init__.py
│   ├── pnl.py                ← PnLTracker — JSONL fill log
│   └── reconciler.py         ← position reconciliation + drift alerts
│
├── utils/
│   ├── __init__.py
│   ├── logger.py             ← structured logging setup
│   └── helpers.py            ← retry decorator, time helpers
│
└── tests/
    ├── conftest.py            ← shared fixtures
    ├── feeds/
    │   ├── test_deribit.py
    │   ├── test_fedwatch.py
    │   └── test_onchain.py
    ├── engine/
    │   ├── test_bayesian.py
    │   ├── test_probability.py
    │   └── test_kl_scanner.py
    ├── market/
    │   ├── test_state.py
    │   └── test_contract_filter.py
    ├── trading/
    │   ├── test_ev_gate.py
    │   ├── test_kelly.py
    │   ├── test_risk.py
    │   └── test_executor.py
    └── tracking/
        ├── test_pnl.py
        └── test_reconciler.py
```

> **Key design constraint:** `market/state.py` is the ONLY place shared mutable state lives. Feeds write to it; engine + trading layers read from it. No globals elsewhere.

---

## Task 1: Project Scaffold

**Files:**
- Create: `polymarket-bot/` directory tree
- Create: `requirements.txt`
- Create: `config.py`
- Create: `market/state.py`
- Create: `utils/logger.py`
- Create: `utils/helpers.py`
- Create: `.env.template`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create project root and directory structure**

```bash
mkdir -p polymarket-bot/{feeds,engine,market,trading,tracking,utils,tests/feeds,tests/engine,tests/market,tests/trading,tests/tracking}
cd polymarket-bot
touch main.py
touch feeds/__init__.py engine/__init__.py market/__init__.py
touch trading/__init__.py tracking/__init__.py utils/__init__.py
touch tests/feeds/__init__.py tests/engine/__init__.py tests/market/__init__.py
touch tests/trading/__init__.py tests/tracking/__init__.py
touch tests/__init__.py tests/feeds/__init__.py tests/engine/__init__.py
touch tests/market/__init__.py tests/trading/__init__.py tests/tracking/__init__.py
```

- [ ] **Step 2: Create `requirements.txt`**

```
py-clob-client>=0.14.0
websockets>=12.0
httpx>=0.27.0
numpy>=1.26.0
scipy>=1.12.0
eth-account>=0.11.0
python-dotenv>=1.0.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 3: Create `config.py`**

```python
import os
from dotenv import load_dotenv

load_dotenv()

# Risk parameters
BANKROLL_USDC = float(os.getenv("BANKROLL_USDC", "500"))
MAX_DAILY_LOSS_PCT = 0.05
MAX_POSITION_PCT = 0.10
MAX_OPEN_POSITIONS = 5
KELLY_FRACTION = 0.25

# EV parameters
MIN_EV_THRESHOLD = 0.03
POLYMARKET_FEE = 0.02
MIN_MARKET_LIQUIDITY = 10_000

# Feed intervals (seconds)
FEDWATCH_POLL_INTERVAL = 60
ONCHAIN_POLL_INTERVAL = 300

# WebSocket URLs
DERIBIT_WS_URL = "wss://www.deribit.com/ws/api/v2"
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLYMARKET_CLOB_URL = "https://clob.polymarket.com"

# Credentials (loaded from .env)
POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
POLY_API_KEY = os.getenv("POLY_API_KEY", "")
GLASSNODE_API_KEY = os.getenv("GLASSNODE_API_KEY", "")
```

- [ ] **Step 4: Create `.env.template`**

```bash
# Copy to .env and fill in — NEVER commit .env
BANKROLL_USDC=500
POLY_PRIVATE_KEY=0x...your_polygon_private_key...
POLY_API_KEY=your_polymarket_api_key
GLASSNODE_API_KEY=your_glassnode_api_key
```

Add `.env` to `.gitignore`:
```bash
echo ".env" >> .gitignore
echo "fills.jsonl" >> .gitignore
echo "__pycache__/" >> .gitignore
echo ".pytest_cache/" >> .gitignore
```

- [ ] **Step 5: Create `market/state.py`**

```python
import asyncio
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FeedState:
    """All feed-derived values. Feeds write here; engine reads here."""
    btc_dvol: Optional[float] = None
    eth_dvol: Optional[float] = None
    btc_price: Optional[float] = None
    eth_price: Optional[float] = None
    # CME FedWatch: meeting date → cut probability
    rate_cut_probs: dict = field(default_factory=dict)
    # On-chain: normalised [0,1] signals
    btc_exchange_netflow: Optional[float] = None
    btc_sopr: Optional[float] = None
    btc_nupl: Optional[float] = None


@dataclass
class ContractState:
    """Live CLOB state for a single Polymarket contract."""
    token_id: str
    question: str
    category: str
    best_bid: float = 0.0
    best_ask: float = 1.0
    volume_usd: float = 0.0

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2


class AppState:
    """Single shared mutable state. Thread-safe via asyncio.Lock."""

    def __init__(self):
        self.feeds = FeedState()
        self.markets: dict[str, ContractState] = {}
        self._lock = asyncio.Lock()

    async def update_feeds(self, **kwargs):
        async with self._lock:
            for k, v in kwargs.items():
                setattr(self.feeds, k, v)

    async def upsert_market(self, state: ContractState):
        async with self._lock:
            self.markets[state.token_id] = state

    async def remove_market(self, token_id: str):
        async with self._lock:
            self.markets.pop(token_id, None)
```

- [ ] **Step 6: Create `utils/logger.py`**

```python
import logging
import sys


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
```

- [ ] **Step 7: Create `utils/helpers.py`**

```python
import asyncio
import functools
from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable)


def async_retry(max_attempts: int = 5, base_delay: float = 1.0):
    """Exponential backoff retry for async functions."""
    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(max_attempts):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_attempts - 1:
                        raise
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60.0)
        return wrapper  # type: ignore
    return decorator
```

- [ ] **Step 8: Create `tests/conftest.py`**

```python
import asyncio
import pytest
from market.state import AppState


@pytest.fixture
def app_state():
    return AppState()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
```

- [ ] **Step 9: Install dependencies and verify imports**

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -c "import websockets, httpx, numpy, eth_account; print('OK')"
```

Expected: `OK`

- [ ] **Step 10: Commit scaffold**

```bash
git init && git add .
git commit -m "feat: project scaffold — config, state, utils, directory structure"
```

---

## Task 2: Bayesian Engine

**Files:**
- Create: `engine/bayesian.py`
- Create: `tests/engine/test_bayesian.py`

The engine is pure math. No I/O. Test first.

- [ ] **Step 1: Write failing tests**

```python
# tests/engine/test_bayesian.py
import numpy as np
import pytest
from engine.bayesian import BayesianEngine


def test_initial_probability_stored_correctly():
    eng = BayesianEngine(prior=0.5)
    assert abs(eng.probability - 0.5) < 1e-9


def test_update_with_lr_gt_1_increases_probability():
    eng = BayesianEngine(prior=0.5)
    eng.update(likelihood_ratio=3.0)  # strong evidence for H
    assert eng.probability > 0.5


def test_update_with_lr_lt_1_decreases_probability():
    eng = BayesianEngine(prior=0.5)
    eng.update(likelihood_ratio=0.3)
    assert eng.probability < 0.5


def test_multiple_updates_accumulate():
    eng = BayesianEngine(prior=0.3)
    eng.update(2.0)
    eng.update(2.0)
    eng.update(2.0)
    assert eng.probability > 0.8


def test_probability_stays_in_01():
    eng = BayesianEngine(prior=0.99)
    for _ in range(100):
        eng.update(1e6)
    assert 0.0 < eng.probability <= 1.0


def test_extreme_prior_near_zero():
    eng = BayesianEngine(prior=0.001)
    assert 0.0 < eng.probability < 0.01


def test_neutral_lr_does_not_change_probability():
    eng = BayesianEngine(prior=0.6)
    before = eng.probability
    eng.update(1.0)
    assert abs(eng.probability - before) < 1e-9


def test_reset_restores_prior():
    eng = BayesianEngine(prior=0.4)
    eng.update(5.0)
    eng.reset()
    assert abs(eng.probability - 0.4) < 1e-9
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/engine/test_bayesian.py -v
```

Expected: `ModuleNotFoundError: No module named 'engine.bayesian'`

- [ ] **Step 3: Implement `engine/bayesian.py`**

```python
import numpy as np


class BayesianEngine:
    def __init__(self, prior: float):
        if not (0 < prior < 1):
            raise ValueError(f"prior must be in (0,1), got {prior}")
        self._prior = prior
        self.log_odds = np.log(prior / (1 - prior))

    def update(self, likelihood_ratio: float):
        """Update belief. likelihood_ratio = P(E|H) / P(E|¬H)."""
        if likelihood_ratio <= 0:
            raise ValueError(f"likelihood_ratio must be > 0, got {likelihood_ratio}")
        self.log_odds += np.log(likelihood_ratio)

    @property
    def probability(self) -> float:
        return float(1 / (1 + np.exp(-self.log_odds)))

    def reset(self):
        self.log_odds = np.log(self._prior / (1 - self._prior))
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/engine/test_bayesian.py -v
```

Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add engine/bayesian.py tests/engine/test_bayesian.py
git commit -m "feat: log-space BayesianEngine with reset"
```

---

## Task 3: Deribit DVOL Feed

**Files:**
- Create: `feeds/base.py`
- Create: `feeds/deribit.py`
- Create: `tests/feeds/test_deribit.py`

Uses public Deribit WebSocket — no API key needed.

- [ ] **Step 1: Write failing tests**

```python
# tests/feeds/test_deribit.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from feeds.deribit import DeribitFeed, parse_dvol_message, parse_ticker_message


def test_parse_dvol_message_btc():
    msg = {
        "method": "subscription",
        "params": {
            "channel": "deribit_volatility_index.btc_usd",
            "data": {"volatility": 72.4, "index_price": 84200.0}
        }
    }
    result = parse_dvol_message(msg)
    assert result == {"btc_dvol": 72.4, "btc_price": 84200.0}


def test_parse_dvol_message_eth():
    msg = {
        "method": "subscription",
        "params": {
            "channel": "deribit_volatility_index.eth_usd",
            "data": {"volatility": 68.1, "index_price": 3200.0}
        }
    }
    result = parse_dvol_message(msg)
    assert result == {"eth_dvol": 68.1, "eth_price": 3200.0}


def test_parse_dvol_message_unknown_channel_returns_none():
    msg = {"method": "subscription", "params": {"channel": "unknown.foo", "data": {}}}
    assert parse_dvol_message(msg) is None


def test_parse_dvol_message_non_subscription_returns_none():
    msg = {"method": "heartbeat", "params": {}}
    assert parse_dvol_message(msg) is None


def test_daily_move_formula():
    """dvol / 20 gives expected daily move in percent."""
    dvol = 80.0
    expected_daily_move_pct = dvol / 20
    assert expected_daily_move_pct == 4.0
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/feeds/test_deribit.py -v
```

Expected: `ImportError`

- [ ] **Step 3: Create `feeds/base.py`**

```python
import asyncio
from abc import ABC, abstractmethod
from utils.logger import get_logger


class BaseFeed(ABC):
    def __init__(self, name: str):
        self.name = name
        self.log = get_logger(name)
        self._running = False

    @abstractmethod
    async def _run(self):
        """Override: main feed coroutine."""

    async def start(self):
        self._running = True
        self.log.info("starting")
        while self._running:
            try:
                await self._run()
            except Exception as exc:
                self.log.warning(f"feed error: {exc} — reconnecting in 5s")
                await asyncio.sleep(5)

    def stop(self):
        self._running = False
```

- [ ] **Step 4: Implement `feeds/deribit.py`**

```python
import json
import asyncio
import websockets
from typing import Optional
from config import DERIBIT_WS_URL
from feeds.base import BaseFeed
from market.state import AppState

_DVOL_SUBSCRIPTIONS = [
    "deribit_volatility_index.btc_usd",
    "deribit_volatility_index.eth_usd",
]

_SUBSCRIBE_MSG = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "public/subscribe",
    "params": {"channels": _DVOL_SUBSCRIPTIONS},
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


def parse_ticker_message(msg: dict) -> Optional[dict]:
    # Reserved for per-strike IV — extend later
    return None


class DeribitFeed(BaseFeed):
    def __init__(self, state: AppState):
        super().__init__("deribit")
        self._state = state

    async def _run(self):
        async with websockets.connect(DERIBIT_WS_URL, ping_interval=30) as ws:
            await ws.send(json.dumps(_SUBSCRIBE_MSG))
            self.log.info("subscribed to DVOL channels")
            async for raw in ws:
                msg = json.loads(raw)
                update = parse_dvol_message(msg)
                if update:
                    await self._state.update_feeds(**update)
                    self.log.debug(f"feed update: {update}")
```

- [ ] **Step 5: Run to verify PASS**

```bash
pytest tests/feeds/test_deribit.py -v
```

Expected: 5 passed

- [ ] **Step 6: Smoke test against live Deribit (manual, no assertion)**

```bash
python -c "
import asyncio
from market.state import AppState
from feeds.deribit import DeribitFeed

async def main():
    state = AppState()
    feed = DeribitFeed(state)
    task = asyncio.create_task(feed.start())
    await asyncio.sleep(10)
    feed.stop()
    print('btc_dvol:', state.feeds.btc_dvol)
    print('btc_price:', state.feeds.btc_price)

asyncio.run(main())
"
```

Expected: prints non-None DVOL value.

- [ ] **Step 7: Commit**

```bash
git add feeds/base.py feeds/deribit.py tests/feeds/test_deribit.py
git commit -m "feat: DeribitFeed — DVOL WebSocket with auto-reconnect"
```

---

## Task 4: CME FedWatch Feed

**Files:**
- Create: `feeds/fedwatch.py`
- Create: `tests/feeds/test_fedwatch.py`

**Note:** CME FedWatch does not have a stable public JSON endpoint. Poll the CME website HTML or use an unofficial scraper. This implementation polls `https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html` — if CME changes the structure, the parser must be updated. Consider FedWatch data as "soft" signal only.

- [ ] **Step 1: Write failing tests**

```python
# tests/feeds/test_fedwatch.py
import pytest
from feeds.fedwatch import parse_fedwatch_response


def test_parse_returns_dict_with_meeting_keys():
    # Stub the shape returned by our parser
    raw = [
        {"meeting": "2025-05-07", "cut_prob": 0.34},
        {"meeting": "2025-06-18", "cut_prob": 0.61},
    ]
    result = parse_fedwatch_response(raw)
    assert "2025-05-07" in result
    assert abs(result["2025-05-07"] - 0.34) < 1e-6


def test_parse_empty_returns_empty_dict():
    assert parse_fedwatch_response([]) == {}


def test_parse_probabilities_clamped_to_01():
    raw = [{"meeting": "2025-05-07", "cut_prob": 1.5}]
    result = parse_fedwatch_response(raw)
    assert result["2025-05-07"] <= 1.0


def test_parse_ignores_negative_probs():
    raw = [{"meeting": "2025-05-07", "cut_prob": -0.1}]
    result = parse_fedwatch_response(raw)
    assert result["2025-05-07"] >= 0.0
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/feeds/test_fedwatch.py -v
```

- [ ] **Step 3: Implement `feeds/fedwatch.py`**

```python
import asyncio
import httpx
from typing import Optional
from config import FEDWATCH_POLL_INTERVAL
from feeds.base import BaseFeed
from market.state import AppState
from utils.logger import get_logger

log = get_logger("fedwatch")

# CME FedWatch API — returns JSON with meeting probabilities
_FEDWATCH_URL = "https://www.cmegroup.com/CmeWS/mvc/FedWatch/tool/meetingprobability"


def parse_fedwatch_response(raw: list) -> dict:
    """Convert list of {meeting, cut_prob} dicts → {date_str: probability}."""
    result = {}
    for item in raw:
        meeting = item.get("meeting", "")
        prob = item.get("cut_prob", 0.0)
        if meeting:
            result[meeting] = max(0.0, min(1.0, float(prob)))
    return result


class FedWatchFeed(BaseFeed):
    def __init__(self, state: AppState):
        super().__init__("fedwatch")
        self._state = state

    async def _run(self):
        async with httpx.AsyncClient(timeout=10.0) as client:
            while self._running:
                try:
                    probs = await self._fetch(client)
                    if probs:
                        await self._state.update_feeds(rate_cut_probs=probs)
                        self.log.info(f"rate_cut_probs updated: {probs}")
                except Exception as exc:
                    self.log.warning(f"fetch error: {exc}")
                await asyncio.sleep(FEDWATCH_POLL_INTERVAL)

    async def _fetch(self, client: httpx.AsyncClient) -> Optional[dict]:
        # CME returns JSON — structure may change; parse defensively
        resp = await client.get(_FEDWATCH_URL)
        resp.raise_for_status()
        data = resp.json()
        # CME JSON shape: list of {meetingDate, probCut25, probCut50, probHold, ...}
        # We derive "probability of ANY cut" = 1 - probHold
        meetings = []
        for entry in data:
            date = entry.get("meetingDate", "")
            hold = float(entry.get("probHold", 100)) / 100
            meetings.append({"meeting": date, "cut_prob": round(1 - hold, 4)})
        return parse_fedwatch_response(meetings)
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/feeds/test_fedwatch.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add feeds/fedwatch.py tests/feeds/test_fedwatch.py
git commit -m "feat: FedWatchFeed — CME rate cut probabilities via REST"
```

---

## Task 5: On-Chain Feed (Glassnode)

**Files:**
- Create: `feeds/onchain.py`
- Create: `tests/feeds/test_onchain.py`

Glassnode free tier: BTC exchange netflow, SOPR, NUPL. All normalised to [0, 1] before entering FeedState.

- [ ] **Step 1: Write failing tests**

```python
# tests/feeds/test_onchain.py
from feeds.onchain import normalise_signal, parse_glassnode_timeseries


def test_normalise_positive_netflow_maps_to_above_half():
    # Positive netflow (BTC flowing to exchanges) → bearish → low signal
    # We define: high outflow (negative netflow) = bullish = high signal
    result = normalise_signal(value=5000, historical_min=-10000, historical_max=10000)
    # 5000 in range [-10000, 10000]: raw = (5000+10000)/20000 = 0.75 → bearish, so signal = 1-0.75 = 0.25
    assert abs(result - 0.25) < 0.01


def test_normalise_clamps_above_max():
    result = normalise_signal(value=99999, historical_min=0, historical_max=100)
    assert result == 0.0  # very high inflow = bearish = low signal


def test_normalise_clamps_below_min():
    result = normalise_signal(value=-99999, historical_min=0, historical_max=100)
    assert result == 1.0  # very high outflow = bullish = high signal


def test_parse_glassnode_timeseries_returns_latest():
    ts = [
        {"t": 1700000000, "v": 0.3},
        {"t": 1700086400, "v": 0.7},
    ]
    assert parse_glassnode_timeseries(ts) == 0.7


def test_parse_glassnode_timeseries_empty_returns_none():
    assert parse_glassnode_timeseries([]) is None
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/feeds/test_onchain.py -v
```

- [ ] **Step 3: Implement `feeds/onchain.py`**

```python
import asyncio
import httpx
from typing import Optional
from config import ONCHAIN_POLL_INTERVAL, GLASSNODE_API_KEY
from feeds.base import BaseFeed
from market.state import AppState

_BASE = "https://api.glassnode.com/v1/metrics"

# Historical ranges for normalisation (approximate; update periodically)
_NETFLOW_MIN, _NETFLOW_MAX = -15000, 15000  # BTC/day


def normalise_signal(value: float, historical_min: float, historical_max: float) -> float:
    """Map value to [0,1]. High = bullish for BTC price. Inverted for inflows."""
    raw = (value - historical_min) / (historical_max - historical_min)
    raw = max(0.0, min(1.0, raw))
    return round(1.0 - raw, 4)  # invert: high inflow → low bullish signal


def parse_glassnode_timeseries(data: list) -> Optional[float]:
    if not data:
        return None
    return data[-1]["v"]


class OnChainFeed(BaseFeed):
    def __init__(self, state: AppState):
        super().__init__("onchain")
        self._state = state
        self._client: Optional[httpx.AsyncClient] = None

    async def _run(self):
        async with httpx.AsyncClient(timeout=15.0) as client:
            self._client = client
            while self._running:
                await self._fetch_all()
                await asyncio.sleep(ONCHAIN_POLL_INTERVAL)

    async def _fetch_all(self):
        if not GLASSNODE_API_KEY:
            self.log.warning("GLASSNODE_API_KEY not set — skipping on-chain feed")
            return
        try:
            netflow = await self._fetch_metric("transactions/transfers_volume_exchanges_net")
            if netflow is not None:
                signal = normalise_signal(netflow, _NETFLOW_MIN, _NETFLOW_MAX)
                await self._state.update_feeds(btc_exchange_netflow=signal)
                self.log.info(f"btc_exchange_netflow={signal:.3f} (raw={netflow:.0f})")
        except Exception as exc:
            self.log.warning(f"on-chain fetch error: {exc}")

    async def _fetch_metric(self, path: str) -> Optional[float]:
        resp = await self._client.get(
            f"{_BASE}/{path}",
            params={"a": "BTC", "i": "24h", "api_key": GLASSNODE_API_KEY},
        )
        resp.raise_for_status()
        return parse_glassnode_timeseries(resp.json())
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/feeds/test_onchain.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add feeds/onchain.py tests/feeds/test_onchain.py
git commit -m "feat: OnChainFeed — Glassnode BTC netflow normalised to [0,1]"
```

---

## Task 6: Contract Filter + CLOB Monitor

**Files:**
- Create: `market/contract_filter.py`
- Create: `market/clob_monitor.py`
- Create: `tests/market/test_contract_filter.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/market/test_contract_filter.py
from market.contract_filter import is_crypto_finance_market, meets_liquidity_threshold
from market.state import ContractState


def test_crypto_category_passes():
    assert is_crypto_finance_market({"category": "crypto"}) is True


def test_finance_category_passes():
    assert is_crypto_finance_market({"category": "finance"}) is True


def test_politics_category_rejected():
    assert is_crypto_finance_market({"category": "politics"}) is False


def test_sports_category_rejected():
    assert is_crypto_finance_market({"category": "sports"}) is False


def test_case_insensitive():
    assert is_crypto_finance_market({"category": "CRYPTO"}) is True


def test_meets_liquidity_above_threshold():
    cs = ContractState(token_id="x", question="q", category="crypto", volume_usd=15_000)
    assert meets_liquidity_threshold(cs, min_usd=10_000) is True


def test_meets_liquidity_below_threshold():
    cs = ContractState(token_id="x", question="q", category="crypto", volume_usd=5_000)
    assert meets_liquidity_threshold(cs, min_usd=10_000) is False
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/market/test_contract_filter.py -v
```

- [ ] **Step 3: Implement `market/contract_filter.py`**

```python
from market.state import ContractState

_ALLOWED_CATEGORIES = {"crypto", "finance"}


def is_crypto_finance_market(market: dict) -> bool:
    return market.get("category", "").lower() in _ALLOWED_CATEGORIES


def meets_liquidity_threshold(contract: ContractState, min_usd: float = 10_000) -> bool:
    return contract.volume_usd >= min_usd
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/market/test_contract_filter.py -v
```

Expected: 7 passed

- [ ] **Step 5: Implement `market/clob_monitor.py`**

```python
import json
import asyncio
import websockets
from config import POLYMARKET_WS_URL, MIN_MARKET_LIQUIDITY
from feeds.base import BaseFeed
from market.state import AppState, ContractState
from market.contract_filter import is_crypto_finance_market, meets_liquidity_threshold

_SUBSCRIBE_MSG = {"type": "subscribe", "channel": "market"}


class CLOBMonitor(BaseFeed):
    """Maintains live ContractState for all crypto/finance Polymarket markets."""

    def __init__(self, state: AppState):
        super().__init__("clob_monitor")
        self._state = state

    async def _run(self):
        async with websockets.connect(POLYMARKET_WS_URL, ping_interval=20) as ws:
            await ws.send(json.dumps(_SUBSCRIBE_MSG))
            self.log.info("subscribed to Polymarket CLOB WebSocket")
            async for raw in ws:
                msg = json.loads(raw)
                await self._handle(msg)

    async def _handle(self, msg: dict):
        event_type = msg.get("event_type", "")
        if event_type == "book":
            await self._handle_book(msg)
        elif event_type == "price_change":
            await self._handle_price(msg)

    async def _handle_book(self, msg: dict):
        market = msg.get("market", {})
        if not is_crypto_finance_market(market):
            return
        token_id = msg.get("asset_id", "")
        if not token_id:
            return
        bids = msg.get("bids", [])
        asks = msg.get("asks", [])
        best_bid = float(bids[0]["price"]) if bids else 0.0
        best_ask = float(asks[0]["price"]) if asks else 1.0
        cs = ContractState(
            token_id=token_id,
            question=market.get("question", ""),
            category=market.get("category", "").lower(),
            best_bid=best_bid,
            best_ask=best_ask,
            volume_usd=float(market.get("volume", 0)),
        )
        if meets_liquidity_threshold(cs, MIN_MARKET_LIQUIDITY):
            await self._state.upsert_market(cs)

    async def _handle_price(self, msg: dict):
        token_id = msg.get("asset_id", "")
        if not token_id:
            return
        async with self._state._lock:
            cs = self._state.markets.get(token_id)
            if cs:
                side = msg.get("side", "")
                price = float(msg.get("price", 0))
                if side == "BUY":
                    cs.best_bid = price
                elif side == "SELL":
                    cs.best_ask = price
```

- [ ] **Step 6: Write tests for CLOBMonitor message parsing**

```python
# tests/market/test_clob_monitor.py
import asyncio
import pytest
from market.state import AppState
from market.clob_monitor import CLOBMonitor


@pytest.fixture
def monitor():
    return CLOBMonitor(AppState())


@pytest.mark.asyncio
async def test_handle_book_adds_crypto_market(monitor):
    msg = {
        "event_type": "book",
        "asset_id": "tok123",
        "market": {"category": "crypto", "question": "BTC > $90k?", "volume": "15000"},
        "bids": [{"price": "0.48"}],
        "asks": [{"price": "0.52"}],
    }
    await monitor._handle(msg)
    assert "tok123" in monitor._state.markets


@pytest.mark.asyncio
async def test_handle_book_ignores_thin_market(monitor):
    msg = {
        "event_type": "book",
        "asset_id": "tok456",
        "market": {"category": "crypto", "question": "BTC > $90k?", "volume": "500"},
        "bids": [{"price": "0.48"}],
        "asks": [{"price": "0.52"}],
    }
    await monitor._handle(msg)
    assert "tok456" not in monitor._state.markets


@pytest.mark.asyncio
async def test_handle_book_ignores_non_crypto(monitor):
    msg = {
        "event_type": "book",
        "asset_id": "tok789",
        "market": {"category": "politics", "question": "Biden wins?", "volume": "50000"},
        "bids": [{"price": "0.40"}],
        "asks": [{"price": "0.60"}],
    }
    await monitor._handle(msg)
    assert "tok789" not in monitor._state.markets


@pytest.mark.asyncio
async def test_handle_price_updates_bid(monitor):
    # First seed a market
    book_msg = {
        "event_type": "book",
        "asset_id": "tok123",
        "market": {"category": "crypto", "question": "BTC > $90k?", "volume": "15000"},
        "bids": [{"price": "0.48"}],
        "asks": [{"price": "0.52"}],
    }
    await monitor._handle(book_msg)
    # Now send price update
    price_msg = {"event_type": "price_change", "asset_id": "tok123", "side": "BUY", "price": "0.51"}
    await monitor._handle(price_msg)
    assert abs(monitor._state.markets["tok123"].best_bid - 0.51) < 1e-9
```

- [ ] **Step 7: Run CLOBMonitor tests**

```bash
pytest tests/market/ -v
```

Expected: 7 passed (contract_filter × 7 + clob_monitor × 4)

- [ ] **Step 8: Commit**

```bash
git add market/contract_filter.py market/clob_monitor.py tests/market/test_contract_filter.py tests/market/test_clob_monitor.py
git commit -m "feat: ContractFilter + CLOBMonitor — live Polymarket CLOB state"
```

---

## Task 7: Probability Module

**Files:**
- Create: `engine/probability.py`
- Create: `tests/engine/test_probability.py`

Converts FeedState to a model probability for a given contract. This is the key discretionary logic — start simple, calibrate over time.

- [ ] **Step 1: Write failing tests**

```python
# tests/engine/test_probability.py
import pytest
from market.state import FeedState, ContractState
from engine.probability import (
    dvol_to_daily_move_pct,
    dvol_to_target_prob,
    build_likelihood_ratio_deribit,
    compute_model_probability,
)


def test_dvol_to_daily_move():
    assert abs(dvol_to_daily_move_pct(80.0) - 4.0) < 0.01
    assert abs(dvol_to_daily_move_pct(20.0) - 1.0) < 0.01


def test_dvol_to_target_prob_above_target():
    """BTC at 84000, target 85000 (~1.2% above). dvol=40 → daily_move=2%.
    Target is 0.6 stdev above current price → prob < 0.5 of being hit."""
    prob = dvol_to_target_prob(current=84000, target=85000, dvol=40.0)
    assert 0.0 < prob < 0.5


def test_dvol_to_target_prob_below_target():
    """Target well below current price → high probability."""
    prob = dvol_to_target_prob(current=84000, target=70000, dvol=40.0)
    assert prob > 0.5


def test_dvol_to_target_prob_at_current_price():
    """Target == current price → probability ~0.5."""
    prob = dvol_to_target_prob(current=84000, target=84000, dvol=40.0)
    assert abs(prob - 0.5) < 0.05


def test_compute_model_probability_returns_float():
    feeds = FeedState(btc_dvol=60.0, btc_price=84000.0)
    contract = ContractState(
        token_id="abc", question="BTC above $90k by June?",
        category="crypto", best_bid=0.3, best_ask=0.35
    )
    result = compute_model_probability(feeds, contract, prior=0.35)
    assert isinstance(result, float)
    assert 0.0 < result < 1.0


def test_compute_model_probability_with_no_feeds_returns_prior():
    feeds = FeedState()  # all None
    contract = ContractState(
        token_id="abc", question="BTC above $90k?",
        category="crypto", best_bid=0.3, best_ask=0.35
    )
    result = compute_model_probability(feeds, contract, prior=0.4)
    assert abs(result - 0.4) < 0.01
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/engine/test_probability.py -v
```

- [ ] **Step 3: Implement `engine/probability.py`**

```python
"""
Converts raw feed data to model probabilities for Polymarket contracts.

Starting approach: log-normal daily move model from DVOL.
- Each contract has an implied target price embedded in its question.
- Probability is computed as: P(BTC closes above target | log-normal distribution
  parameterised by current price + DVOL).
- If target cannot be parsed, returns the prior unchanged.

Extend with FedWatch and on-chain signals once Deribit baseline is validated.
"""

import re
import numpy as np
from typing import Optional
from market.state import FeedState, ContractState
from engine.bayesian import BayesianEngine


def dvol_to_daily_move_pct(dvol: float) -> float:
    """Annualised vol → approximate daily move in percent. dvol/20."""
    return dvol / 20.0


def dvol_to_target_prob(current: float, target: float, dvol: float) -> float:
    """
    Log-normal probability that asset closes at or above `target` given current price
    and annualised vol (DVOL). Uses 1-day horizon.
    """
    daily_vol = dvol_to_daily_move_pct(dvol) / 100.0  # fraction
    log_return_needed = np.log(target / current)
    # Z-score under N(0, daily_vol)
    z = log_return_needed / daily_vol if daily_vol > 0 else float("inf")
    # Probability of being above target = P(X > z) where X ~ N(0,1)
    from scipy.stats import norm  # local import — add scipy to requirements.txt
    return float(norm.sf(z))  # survival function = 1 - CDF


def _parse_btc_target(question: str) -> Optional[float]:
    """Extract BTC price target from contract question string."""
    # Matches: "BTC above $85,000", "Bitcoin above $85k", "BTC > $90000"
    match = re.search(r"\$\s?([\d,]+)k?", question, re.IGNORECASE)
    if not match:
        return None
    raw = match.group(1).replace(",", "")
    value = float(raw)
    if "k" in match.group(0).lower():
        value *= 1000
    return value


def build_likelihood_ratio_deribit(
    feeds: FeedState, contract: ContractState, prior: float
) -> Optional[float]:
    """
    Compute likelihood ratio from Deribit DVOL for a BTC price contract.
    Returns None if required data is unavailable or contract is not BTC-price type.
    """
    if feeds.btc_dvol is None or feeds.btc_price is None:
        return None
    target = _parse_btc_target(contract.question)
    if target is None:
        return None
    model_prob = dvol_to_target_prob(feeds.btc_price, target, feeds.btc_dvol)
    # Likelihood ratio: P(this data | H=YES) / P(this data | H=NO)
    # Approximate: model_prob / prior (calibrate this later)
    if prior <= 0 or prior >= 1:
        return None
    return model_prob / prior


def compute_model_probability(
    feeds: FeedState, contract: ContractState, prior: float
) -> float:
    """
    Derive model probability for a contract. Falls back to prior if no feed
    data is applicable.
    """
    eng = BayesianEngine(prior=prior)
    lr = build_likelihood_ratio_deribit(feeds, contract, prior)
    if lr is not None and lr > 0:
        eng.update(lr)
    return eng.probability
```

Add `scipy` to `requirements.txt`:
```
scipy>=1.12.0
```
Install: `pip install scipy`

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/engine/test_probability.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add engine/probability.py tests/engine/test_probability.py requirements.txt
git commit -m "feat: probability module — DVOL log-normal model for BTC contracts"
```

---

## Task 8: EV Gate (Paper Mode)

**Files:**
- Create: `trading/ev_gate.py`
- Create: `tests/trading/test_ev_gate.py`

Paper mode = log opportunities, do not execute. Run this phase for several days before enabling the executor.

- [ ] **Step 1: Write failing tests**

```python
# tests/trading/test_ev_gate.py
import pytest
from trading.ev_gate import calculate_ev, should_enter, EVOpportunity


def test_positive_ev_when_model_above_cost():
    # model says 0.60 true prob, market price 0.50, fee 0.02 → cost=0.52, EV=0.08
    ev = calculate_ev(model_prob=0.60, market_price=0.50)
    assert abs(ev - 0.08) < 1e-9


def test_negative_ev_when_model_below_cost():
    ev = calculate_ev(model_prob=0.50, market_price=0.55)
    # cost=0.57, ev = 0.50-0.57 = -0.07
    assert ev < 0


def test_zero_ev_at_breakeven():
    # cost = model_prob → EV = 0
    # market_price = model_prob - fee
    ev = calculate_ev(model_prob=0.52, market_price=0.50)
    assert abs(ev) < 0.01


def test_should_enter_above_threshold():
    assert should_enter(ev=0.05, min_ev_threshold=0.03) is True


def test_should_enter_at_threshold():
    assert should_enter(ev=0.03, min_ev_threshold=0.03) is False  # must EXCEED


def test_should_enter_below_threshold():
    assert should_enter(ev=0.01, min_ev_threshold=0.03) is False


def test_ev_opportunity_contains_required_fields():
    opp = EVOpportunity(
        token_id="abc", question="BTC > $85k?",
        model_prob=0.60, market_price=0.50, ev=0.08
    )
    assert opp.edge == pytest.approx(0.10, abs=1e-6)
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/trading/test_ev_gate.py -v
```

- [ ] **Step 3: Implement `trading/ev_gate.py`**

```python
from dataclasses import dataclass
from config import POLYMARKET_FEE, MIN_EV_THRESHOLD


@dataclass
class EVOpportunity:
    token_id: str
    question: str
    model_prob: float
    market_price: float
    ev: float

    @property
    def edge(self) -> float:
        return self.model_prob - self.market_price


def calculate_ev(model_prob: float, market_price: float,
                 fee: float = POLYMARKET_FEE) -> float:
    """EV of buying YES at market_price given model_prob. Payout = $1."""
    cost = market_price + fee
    return model_prob - cost


def should_enter(ev: float, min_ev_threshold: float = MIN_EV_THRESHOLD) -> bool:
    return ev > min_ev_threshold
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/trading/test_ev_gate.py -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add trading/ev_gate.py tests/trading/test_ev_gate.py
git commit -m "feat: EVGate — EV calculation + entry filter with fee adjustment"
```

---

## Task 9: Kelly Sizing

**Files:**
- Create: `trading/kelly.py`
- Create: `tests/trading/test_kelly.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/trading/test_kelly.py
import pytest
from trading.kelly import fractional_kelly, kelly_dollars


def test_kelly_positive_edge():
    # p=0.6, market_price=0.5 → b=(1-0.5)/0.5=1.0
    # full kelly = (0.6*1 - 0.4)/1 = 0.2
    # quarter kelly = 0.05
    frac = fractional_kelly(model_prob=0.6, market_price=0.5, kelly_fraction=0.25)
    assert abs(frac - 0.05) < 1e-9


def test_kelly_no_edge_returns_zero():
    frac = fractional_kelly(model_prob=0.5, market_price=0.5, kelly_fraction=0.25)
    assert frac == 0.0


def test_kelly_negative_full_kelly_clamped_to_zero():
    # p=0.3, b=1.0 → full kelly = (0.3-0.7)/1 = -0.4 → clamp to 0
    frac = fractional_kelly(model_prob=0.3, market_price=0.5, kelly_fraction=0.25)
    assert frac == 0.0


def test_kelly_dollars_scales_by_bankroll():
    frac = fractional_kelly(0.6, 0.5, 0.25)
    dollars = kelly_dollars(frac, bankroll=1000.0)
    assert abs(dollars - 50.0) < 1e-6


def test_kelly_fraction_never_exceeds_max_position():
    # Even with absurd edge, dollar size capped by caller
    frac = fractional_kelly(model_prob=0.99, market_price=0.01, kelly_fraction=0.25)
    assert frac <= 1.0


def test_kelly_half_fraction():
    frac = fractional_kelly(model_prob=0.6, market_price=0.5, kelly_fraction=0.5)
    assert abs(frac - 0.10) < 1e-9
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/trading/test_kelly.py -v
```

- [ ] **Step 3: Implement `trading/kelly.py`**

```python
from config import KELLY_FRACTION


def fractional_kelly(
    model_prob: float,
    market_price: float,
    kelly_fraction: float = KELLY_FRACTION,
) -> float:
    """
    Returns fraction of bankroll to wager.
    b = net odds per $1 risked = (1 - market_price) / market_price
    f* = (p*b - q) / b  → clamped to [0, 1]
    """
    p = model_prob
    q = 1 - p
    b = (1 - market_price) / market_price  # net payout odds
    full_kelly = (p * b - q) / b
    full_kelly = max(0.0, min(1.0, full_kelly))
    return full_kelly * kelly_fraction


def kelly_dollars(kelly_fraction: float, bankroll: float) -> float:
    return kelly_fraction * bankroll
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/trading/test_kelly.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add trading/kelly.py tests/trading/test_kelly.py
git commit -m "feat: fractional Kelly sizing (¼ Kelly default)"
```

---

## Task 10: Risk Manager

**Files:**
- Create: `trading/risk.py`
- Create: `tests/trading/test_risk.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/trading/test_risk.py
import pytest
from trading.risk import RiskManager


@pytest.fixture
def rm():
    return RiskManager(bankroll=1000.0, max_daily_loss_pct=0.05, max_position_pct=0.10)


def test_can_trade_initially(rm):
    ok, msg = rm.can_trade("abc", position_size=50.0)
    assert ok is True


def test_rejects_oversized_position(rm):
    ok, msg = rm.can_trade("abc", position_size=150.0)  # >10% of 1000
    assert ok is False
    assert "exceeds max" in msg


def test_rejects_after_daily_loss_limit(rm):
    rm.record_pnl(-51.0)  # -5.1% of 1000
    ok, msg = rm.can_trade("abc", position_size=10.0)
    assert ok is False
    assert "Daily loss limit" in msg


def test_allows_max_open_positions(rm):
    for i in range(5):
        rm.open_position(f"contract_{i}", size=20.0)
    ok, _ = rm.can_trade("new_contract", position_size=20.0)
    assert ok is False


def test_close_position_frees_slot(rm):
    for i in range(5):
        rm.open_position(f"contract_{i}", size=20.0)
    rm.close_position("contract_0", pnl=5.0)
    ok, _ = rm.can_trade("new_contract", position_size=20.0)
    assert ok is True


def test_record_pnl_accumulates(rm):
    rm.record_pnl(-20.0)
    rm.record_pnl(-20.0)
    assert rm.daily_pnl == pytest.approx(-40.0)


def test_reset_daily_clears_pnl(rm):
    rm.record_pnl(-40.0)
    rm.reset_daily()
    assert rm.daily_pnl == 0.0
    ok, _ = rm.can_trade("abc", 10.0)
    assert ok is True
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/trading/test_risk.py -v
```

- [ ] **Step 3: Implement `trading/risk.py`**

```python
from config import MAX_DAILY_LOSS_PCT, MAX_POSITION_PCT, MAX_OPEN_POSITIONS


class RiskManager:
    def __init__(
        self,
        bankroll: float,
        max_daily_loss_pct: float = MAX_DAILY_LOSS_PCT,
        max_position_pct: float = MAX_POSITION_PCT,
        max_open_positions: int = MAX_OPEN_POSITIONS,
    ):
        self.bankroll = bankroll
        self._max_daily_loss = bankroll * max_daily_loss_pct
        self._max_position = bankroll * max_position_pct
        self._max_open = max_open_positions
        self.daily_pnl = 0.0
        self._positions: dict[str, float] = {}  # token_id → size

    def can_trade(self, contract_id: str, position_size: float) -> tuple[bool, str]:
        if self.daily_pnl <= -self._max_daily_loss:
            return False, "Daily loss limit hit — bot halted for today"
        if position_size > self._max_position:
            return False, f"Position size ${position_size:.0f} exceeds max ${self._max_position:.0f}"
        if len(self._positions) >= self._max_open:
            return False, f"Max open positions ({self._max_open}) reached"
        return True, "ok"

    def open_position(self, contract_id: str, size: float):
        self._positions[contract_id] = size

    def close_position(self, contract_id: str, pnl: float):
        self._positions.pop(contract_id, None)
        self.record_pnl(pnl)

    def record_pnl(self, pnl: float):
        self.daily_pnl += pnl

    def reset_daily(self):
        self.daily_pnl = 0.0
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/trading/test_risk.py -v
```

Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add trading/risk.py tests/trading/test_risk.py
git commit -m "feat: RiskManager — daily loss limit, position limits, open position cap"
```

---

## Task 11: P&L Tracker

**Files:**
- Create: `tracking/pnl.py`
- Create: `tests/tracking/test_pnl.py`

Log every fill to JSONL before the executor goes live. This is your calibration dataset.

- [ ] **Step 1: Write failing tests**

```python
# tests/tracking/test_pnl.py
import json
import pytest
from pathlib import Path
from tracking.pnl import PnLTracker


@pytest.fixture
def tracker(tmp_path):
    return PnLTracker(log_file=str(tmp_path / "fills.jsonl"))


def test_log_fill_writes_jsonl(tracker, tmp_path):
    tracker.log_fill(
        contract_id="abc", side="BUY", size=50.0,
        price=0.50, model_prob=0.60, market_prob=0.50
    )
    lines = (tmp_path / "fills.jsonl").read_text().strip().split("\n")
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["contract"] == "abc"
    assert record["side"] == "BUY"
    assert abs(record["edge"] - 0.10) < 1e-9


def test_multiple_fills_append(tracker, tmp_path):
    for _ in range(3):
        tracker.log_fill("x", "BUY", 10.0, 0.4, 0.5, 0.4)
    lines = (tmp_path / "fills.jsonl").read_text().strip().split("\n")
    assert len(lines) == 3


def test_record_contains_timestamp(tracker, tmp_path):
    tracker.log_fill("x", "BUY", 10.0, 0.4, 0.5, 0.4)
    record = json.loads((tmp_path / "fills.jsonl").read_text())
    assert "ts" in record


def test_realised_ev_tracked(tracker, tmp_path):
    tracker.log_fill("x", "BUY", 10.0, 0.5, 0.6, 0.5)
    record = json.loads((tmp_path / "fills.jsonl").read_text())
    assert abs(record["edge"] - 0.1) < 1e-9
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/tracking/test_pnl.py -v
```

- [ ] **Step 3: Implement `tracking/pnl.py`**

```python
import json
from datetime import datetime, timezone
from pathlib import Path


class PnLTracker:
    def __init__(self, log_file: str = "fills.jsonl"):
        self._path = Path(log_file)

    def log_fill(
        self,
        contract_id: str,
        side: str,
        size: float,
        price: float,
        model_prob: float,
        market_prob: float,
    ):
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "contract": contract_id,
            "side": side,
            "size": size,
            "price": price,
            "model_prob": model_prob,
            "market_prob": market_prob,
            "edge": round(model_prob - market_prob, 6),
        }
        with self._path.open("a") as f:
            f.write(json.dumps(record) + "\n")
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/tracking/test_pnl.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add tracking/pnl.py tests/tracking/test_pnl.py
git commit -m "feat: PnLTracker — JSONL fill log with edge tracking"
```

---

## Task 12: CLOB Executor

**Files:**
- Create: `trading/executor.py`
- Create: `tests/trading/test_executor.py`

Uses `py-clob-client` for signing. **Paper mode flag is mandatory** — default to paper=True until you have verified edge over at least 50 paper signals.

- [ ] **Step 1: Write failing tests**

```python
# tests/trading/test_executor.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from trading.executor import CLOBExecutor, OrderResult


def test_order_result_success():
    r = OrderResult(order_id="123", status="MATCHED", filled_price=0.52, filled_size=50.0)
    assert r.success is True


def test_order_result_failure():
    r = OrderResult(order_id=None, status="ERROR", filled_price=0.0, filled_size=0.0, error="timeout")
    assert r.success is False


@pytest.mark.asyncio
async def test_paper_mode_does_not_call_api():
    executor = CLOBExecutor(private_key="0x" + "a" * 64, paper=True)
    result = await executor.place_order(
        token_id="abc", side="BUY", size=50.0, price=0.50
    )
    assert result.success is True
    assert result.order_id == "PAPER"


@pytest.mark.asyncio
async def test_place_order_validates_side():
    executor = CLOBExecutor(private_key="0x" + "a" * 64, paper=True)
    with pytest.raises(ValueError, match="side must be BUY or SELL"):
        await executor.place_order("abc", side="HOLD", size=10.0, price=0.5)


@pytest.mark.asyncio
async def test_place_order_validates_size():
    executor = CLOBExecutor(private_key="0x" + "a" * 64, paper=True)
    with pytest.raises(ValueError, match="size must be > 0"):
        await executor.place_order("abc", side="BUY", size=-1.0, price=0.5)
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/trading/test_executor.py -v
```

- [ ] **Step 3: Implement `trading/executor.py`**

```python
import httpx
from dataclasses import dataclass
from typing import Optional
from config import POLYMARKET_CLOB_URL, POLY_PRIVATE_KEY
from utils.logger import get_logger

log = get_logger("executor")


@dataclass
class OrderResult:
    order_id: Optional[str]
    status: str
    filled_price: float
    filled_size: float
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.order_id is not None and self.status not in ("ERROR",)


class CLOBExecutor:
    """
    Places orders via Polymarket CLOB API using py-clob-client.
    Set paper=True (default) to log without executing.
    Only set paper=False after verifying edge on paper signals.
    """

    def __init__(self, private_key: str = POLY_PRIVATE_KEY, paper: bool = True):
        self._paper = paper
        self._private_key = private_key
        self._client: Optional[httpx.AsyncClient] = None
        if not paper:
            from py_clob_client.client import ClobClient  # type: ignore
            self._clob = ClobClient(
                host=POLYMARKET_CLOB_URL,
                key=private_key,
                chain_id=137,  # Polygon mainnet
            )

    async def place_order(
        self, token_id: str, side: str, size: float, price: float
    ) -> OrderResult:
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        if size <= 0:
            raise ValueError(f"size must be > 0, got {size}")

        if self._paper:
            log.info(f"PAPER ORDER | {side} {size} @ {price} | token={token_id}")
            return OrderResult(order_id="PAPER", status="PAPER", filled_price=price, filled_size=size)

        try:
            resp = self._clob.create_and_post_order(
                self._clob.create_order(
                    token_id=token_id,
                    price=price,
                    size=size,
                    side=side,
                )
            )
            order_id = resp.get("orderID")
            log.info(f"ORDER PLACED | id={order_id} | {side} {size} @ {price}")
            return OrderResult(
                order_id=order_id,
                status=resp.get("status", "UNKNOWN"),
                filled_price=float(resp.get("price", price)),
                filled_size=float(resp.get("size", size)),
            )
        except Exception as exc:
            log.error(f"order failed: {exc}")
            return OrderResult(order_id=None, status="ERROR", filled_price=0.0, filled_size=0.0, error=str(exc))

    async def cancel_order(self, order_id: str) -> bool:
        if self._paper:
            return True
        try:
            self._clob.cancel(order_id)
            return True
        except Exception as exc:
            log.error(f"cancel failed: {exc}")
            return False
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/trading/test_executor.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add trading/executor.py tests/trading/test_executor.py
git commit -m "feat: CLOBExecutor — paper/live order placement with py-clob-client"
```

---

## Task 13: Main Orchestrator

**Files:**
- Create: `main.py`
- Modify: `requirements.txt` (add `scipy` if not present)

Wire all coroutines. Paper mode by default. The bot logs signals; you review the JSONL before flipping `paper=False`.

- [ ] **Step 1: Implement `main.py`**

```python
"""
Polymarket Bot — Entry Point

Starts all async feed loops and the main trading loop.
Set PAPER=False in .env only after validating edge on paper signals.
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from market.state import AppState
from feeds.deribit import DeribitFeed
from feeds.fedwatch import FedWatchFeed
from feeds.onchain import OnChainFeed
from market.clob_monitor import CLOBMonitor
from engine.probability import compute_model_probability
from trading.ev_gate import calculate_ev, should_enter, EVOpportunity
from trading.kelly import fractional_kelly, kelly_dollars
from trading.risk import RiskManager
from trading.executor import CLOBExecutor
from tracking.pnl import PnLTracker
from config import BANKROLL_USDC, KELLY_FRACTION, MIN_EV_THRESHOLD
from utils.logger import get_logger

log = get_logger("main")

PAPER = os.getenv("PAPER", "true").lower() != "false"
SCAN_INTERVAL = 10  # seconds between opportunity scans


async def trading_loop(state: AppState, risk: RiskManager,
                       executor: CLOBExecutor, tracker: PnLTracker):
    while True:
        await asyncio.sleep(SCAN_INTERVAL)
        async with state._lock:
            markets = dict(state.markets)
            feeds = state.feeds

        for token_id, contract in markets.items():
            prior = contract.mid  # use market price as prior
            if prior <= 0.01 or prior >= 0.99:
                continue  # skip near-resolved contracts

            model_prob = compute_model_probability(feeds, contract, prior)
            ev = calculate_ev(model_prob, contract.mid)

            if not should_enter(ev):
                continue

            size = kelly_dollars(
                fractional_kelly(model_prob, contract.mid, KELLY_FRACTION),
                bankroll=BANKROLL_USDC,
            )
            ok, reason = risk.can_trade(token_id, size)
            if not ok:
                log.info(f"BLOCKED | {contract.question[:60]} | {reason}")
                continue

            opp = EVOpportunity(token_id, contract.question, model_prob, contract.mid, ev)
            log.info(
                f"SIGNAL | EV={ev:.3f} | edge={opp.edge:.3f} | "
                f"size=${size:.0f} | {contract.question[:60]}"
            )

            result = await executor.place_order(token_id, "BUY", size, contract.best_ask)
            if result.success:
                risk.open_position(token_id, size)
                tracker.log_fill(
                    contract_id=token_id,
                    side="BUY",
                    size=size,
                    price=result.filled_price,
                    model_prob=model_prob,
                    market_prob=contract.mid,
                )


async def main():
    log.info(f"starting | paper={PAPER} | bankroll=${BANKROLL_USDC}")
    state = AppState()
    risk = RiskManager(bankroll=BANKROLL_USDC)
    executor = CLOBExecutor(paper=PAPER)
    tracker = PnLTracker()

    await asyncio.gather(
        DeribitFeed(state).start(),
        FedWatchFeed(state).start(),
        OnChainFeed(state).start(),
        CLOBMonitor(state).start(),
        trading_loop(state, risk, executor, tracker),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests pass

- [ ] **Step 3: Start in paper mode**

```bash
cp .env.template .env
# Fill in POLY_PRIVATE_KEY if running CLOB monitor (read-only doesn't need it for now)
python main.py
```

Expected: log output showing DVOL values and CLOB market updates. No orders placed. Signals logged to `fills.jsonl` if EV > 3%.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: main orchestrator — async gather, paper mode, full trading loop"
```

---

## Task 14: KL Scanner (Phase 2 — after paper validation)

**Files:**
- Create: `engine/kl_scanner.py`
- Create: `tests/engine/test_kl_scanner.py`

**Do not build this until you have 50+ paper signals logged and model_prob is directionally correct.**

- [ ] **Step 1: Write failing tests**

```python
# tests/engine/test_kl_scanner.py
import numpy as np
import pytest
from engine.kl_scanner import kl_divergence, ContractPair, KLScanner


def test_kl_divergence_identical_distributions():
    p = [0.5, 0.5]
    assert abs(kl_divergence(p, p)) < 1e-9


def test_kl_divergence_is_asymmetric():
    p = [0.7, 0.3]
    q = [0.4, 0.6]
    assert kl_divergence(p, q) != kl_divergence(q, p)


def test_kl_divergence_positive():
    p = [0.7, 0.3]
    q = [0.4, 0.6]
    assert kl_divergence(p, q) > 0


def test_scanner_detects_mispricing():
    scanner = KLScanner(window=5, z_threshold=2.0)
    pair = ContractPair(
        low_token="btc_85k",
        high_token="btc_90k",
        description="BTC $85k vs $90k March"
    )
    # Feed historical baseline: similar markets
    for _ in range(5):
        scanner.observe(pair, p_low=0.60, p_high=0.40)
    # Now inject anomaly
    alert = scanner.check(pair, p_low=0.90, p_high=0.40)
    assert alert is not None  # should flag divergence


def test_scanner_no_alert_within_normal_range():
    scanner = KLScanner(window=5, z_threshold=2.0)
    pair = ContractPair("a", "b", "test")
    for _ in range(5):
        scanner.observe(pair, p_low=0.60, p_high=0.40)
    alert = scanner.check(pair, p_low=0.61, p_high=0.40)
    assert alert is None
```

- [ ] **Step 2: Run to verify FAIL**

```bash
pytest tests/engine/test_kl_scanner.py -v
```

- [ ] **Step 3: Implement `engine/kl_scanner.py`**

```python
import numpy as np
from collections import deque
from dataclasses import dataclass
from typing import Optional


def kl_divergence(p: list, q: list) -> float:
    """KL(P||Q) = Σ P_i * ln(P_i / Q_i). Assumes valid probability distributions."""
    p_arr = np.array(p, dtype=float)
    q_arr = np.clip(np.array(q, dtype=float), 1e-9, None)
    return float(np.sum(p_arr * np.log(p_arr / q_arr)))


@dataclass
class ContractPair:
    low_token: str   # lower threshold contract (e.g. BTC > $85k)
    high_token: str  # higher threshold contract (e.g. BTC > $90k)
    description: str


@dataclass
class KLAlert:
    pair: ContractPair
    kl: float
    z_score: float
    p_low: float
    p_high: float


class KLScanner:
    def __init__(self, window: int = 20, z_threshold: float = 2.0):
        self._window = window
        self._z = z_threshold
        self._history: dict[str, deque] = {}

    def _key(self, pair: ContractPair) -> str:
        return f"{pair.low_token}|{pair.high_token}"

    def observe(self, pair: ContractPair, p_low: float, p_high: float):
        key = self._key(pair)
        if key not in self._history:
            self._history[key] = deque(maxlen=self._window)
        kl = kl_divergence([p_low, 1 - p_low], [p_high, 1 - p_high])
        self._history[key].append(kl)

    def check(self, pair: ContractPair, p_low: float, p_high: float) -> Optional[KLAlert]:
        key = self._key(pair)
        hist = list(self._history.get(key, []))
        if len(hist) < 3:
            return None
        current_kl = kl_divergence([p_low, 1 - p_low], [p_high, 1 - p_high])
        mean, std = np.mean(hist), np.std(hist)
        if std < 1e-9:
            return None
        z = (current_kl - mean) / std
        if z > self._z:
            return KLAlert(pair=pair, kl=current_kl, z_score=float(z), p_low=p_low, p_high=p_high)
        return None
```

- [ ] **Step 4: Run to verify PASS**

```bash
pytest tests/engine/test_kl_scanner.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add engine/kl_scanner.py tests/engine/test_kl_scanner.py
git commit -m "feat: KLScanner — cross-contract mispricing detector (z-score alert)"
```

---

## Task 15: Final Verification

- [ ] **Step 0: Run in paper mode for 48 hours minimum**

```bash
python main.py
```

Let it run unattended. The bot needs at least 50 signals before you can evaluate edge. At 10-second scan interval on 100+ active markets, expect 20–100 signals/day depending on market conditions.

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests pass, 0 failures

- [ ] **Step 2: Check paper fills log after 48h**

```bash
python -c "
import json
fills = [json.loads(l) for l in open('fills.jsonl')]
edges = [f['edge'] for f in fills]
print(f'Signals: {len(fills)}')
print(f'Mean edge: {sum(edges)/len(edges):.3f}' if fills else 'No signals yet')
"
```

Expected: signals with positive mean edge before enabling live mode.

- [ ] **Step 3: Enable live only when validated**

In `.env`:
```
PAPER=false
```

**Only do this after:**
- 50+ paper signals logged
- Mean edge > 3%
- Calibration plot shows model_prob tracks outcomes directionally

- [ ] **Step 4: Final commit**

```bash
git add .
git commit -m "chore: final polish — all tests passing, paper mode validated"
```

---

## Key Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Polymarket API downtime | `BaseFeed.start()` auto-reconnects with 5s backoff |
| Deribit WebSocket drop | Same — `_run()` exception re-entered automatically |
| Model probability wrong | Paper mode + edge tracking before live. DO NOT skip. |
| Gas/USDC bridging delays | Pre-fund Polygon wallet before starting live |
| Full Kelly blowup | Hard-coded `KELLY_FRACTION = 0.25` — cannot be overridden from env |
| CME FedWatch page structure change | `FedWatchFeed` parser is isolated — update `_fetch` method only |
| Resolution dispute | Avoid contracts with ambiguous wording (manual review of question text) |

---

## Phase 2 — Deferred (after live validation)

These are in the spec but excluded from MVP to avoid scope creep:

- `feeds/mirofish.py` — MiroFish offline swarm simulation (Docker subprocess, complex setup)
- `tracking/reconciler.py` — position reconciliation + base rate calibration
- KL Scanner integration into trading loop (Task 14 implements the detector; wiring it in `main.py` is Phase 2)

Build these only after you have real fill data and have confirmed the core edge is positive.

---

## Paper Trade Validation Checklist (before going live)

- [ ] 50+ signals logged in `fills.jsonl`
- [ ] Mean edge (model_prob − market_prob) > 0.03
- [ ] No systematic bias in one direction (not always BUY/SELL)
- [ ] Daily loss limit never triggered
- [ ] Calibration: when model_prob = 0.60, actual resolution rate ≈ 60%
- [ ] Risk manager correctly tracks open/closed positions
- [ ] Fills.jsonl size reasonable (not spamming marginal opportunities)
