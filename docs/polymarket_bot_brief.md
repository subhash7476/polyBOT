# Polymarket Bot — Claude Code Project Brief
**Version:** 1.0 | **Date:** March 2026 | **Target:** Automated directional trading on Polymarket crypto/finance markets

---

## Goal

Build a fully automated Python trading bot that:
- Monitors Polymarket crypto/finance prediction markets
- Derives probability estimates from external data sources (Deribit IV, CME FedWatch, on-chain data)
- Identifies positive EV opportunities where model probability diverges from market price
- Executes orders via Polymarket CLOB API with proper position sizing and risk management

---

## Project Structure

```
polymarket-bot/
├── main.py                  # Entry point — starts all async loops
├── config.py                # All config, thresholds, constants
├── .env                     # API keys (never commit)
├── requirements.txt
│
├── feeds/
│   ├── deribit.py           # DVOL + options chain WebSocket feed
│   ├── fedwatch.py          # CME FedWatch rate probabilities (REST polling)
│   ├── onchain.py           # Glassnode/Nansen on-chain signals (REST polling)
│   └── mirofish.py          # MiroFish offline swarm simulation runner
│
├── engine/
│   ├── bayesian.py          # Log-space Bayesian update engine
│   ├── probability.py       # Derives contract probability from feed data
│   └── kl_scanner.py        # KL divergence cross-contract mispricing detector
│
├── market/
│   ├── clob_monitor.py      # Polymarket CLOB WebSocket — monitors open contracts
│   └── contract_filter.py   # Filters to crypto/finance markets only
│
├── trading/
│   ├── ev_gate.py           # EV calculation and entry filter
│   ├── kelly.py             # Fractional Kelly position sizing (¼ Kelly)
│   ├── risk.py              # Drawdown limit + position limit enforcement
│   └── executor.py          # Polymarket CLOB order placement
│
├── tracking/
│   ├── pnl.py               # P&L tracker + fill logger
│   └── reconciler.py        # Position reconciliation + base rate calibration
│
└── utils/
    ├── logger.py
    └── helpers.py
```

---

## Architecture (8 Layers)

### Layer 1 — Data Feeds (`feeds/`)

**Deribit IV** (`feeds/deribit.py`)
- Connect to `wss://www.deribit.com/ws/api/v2`
- Subscribe to `deribit_volatility_index.btc_usd` and `deribit_volatility_index.eth_usd`
- Also subscribe to `markprice.options.btc_usd` for per-strike IV
- No API key needed — public endpoint
- Output: `{"btc_dvol": 72.4, "eth_dvol": 68.1, "btc_price": 84200}`
- Expected daily move formula: `dvol / 20` (annualised → daily approximation)

**CME FedWatch** (`feeds/fedwatch.py`)
- Poll CME FedWatch tool API for implied rate cut probabilities by meeting date
- REST polling every 60 seconds is sufficient (data doesn't change faster)
- Output: `{"may_cut_prob": 0.34, "june_cut_prob": 0.61}`

**On-chain data** (`feeds/onchain.py`)
- Glassnode free tier: BTC exchange netflow, SOPR, NUPL
- Nansen (optional): smart money flow signals
- Poll every 5 minutes
- Output: normalised signals in `[0, 1]` range as probability modifiers

**MiroFish** (`feeds/mirofish.py`)
- Run MiroFish-Offline locally via Docker subprocess
- Called once per new contract, not in real-time
- Input: relevant news article or financial report as seed
- Output: sentiment convergence probability for the contract's outcome
- Use as the initial prior in the Bayesian engine before market data refines it

---

### Layer 2 — Bayesian Engine (`engine/bayesian.py`)

**Critical implementation detail: use log-space arithmetic to avoid floating point underflow**

```python
import numpy as np

class BayesianEngine:
    def __init__(self, prior: float):
        # Store log-odds internally
        self.log_odds = np.log(prior / (1 - prior))

    def update(self, likelihood_ratio: float):
        """Update belief given P(E|H) / P(E|not-H)"""
        self.log_odds += np.log(likelihood_ratio)

    @property
    def probability(self) -> float:
        return 1 / (1 + np.exp(-self.log_odds))
```

Each feed produces a likelihood ratio that updates the engine:
- Deribit IV → convert to log-normal probability for the price target
- CME FedWatch → direct probability input for rate-related contracts
- On-chain signals → calibrated likelihood ratios from historical data
- MiroFish → used as initial prior only

---

### Layer 3 — Contract Monitor (`market/`)

**CLOB Monitor** (`market/clob_monitor.py`)
- Connect to Polymarket WebSocket feed
- Subscribe to all open crypto/finance category markets
- Parse order book updates — track best bid/ask and mid price
- Mid price = current market-implied probability for YES shares
- Filter: only track contracts with >$10k liquidity (thin markets = bad fills)

**KL Scanner** (`engine/kl_scanner.py`)
- Maintain a registry of correlated contract pairs
- Examples: "BTC above $85k by March" ↔ "BTC above $90k by March"
- Calculate KL divergence between implied distributions
- Flag when gap exceeds historical norm by >2σ
- Formula: `D_KL(P||Q) = Σ P_i * ln(P_i / Q_i)`

---

### Layer 4 — EV Gate (`trading/ev_gate.py`)

```python
POLYMARKET_FEE = 0.02  # 2% taker fee — verify current rate

def calculate_ev(model_prob: float, market_price: float, payout: float = 1.0) -> float:
    """
    model_prob: your derived probability of YES
    market_price: current Polymarket YES price (0 to 1)
    payout: $1 per share at resolution
    """
    cost = market_price + POLYMARKET_FEE
    ev = (model_prob * payout) - cost
    return ev

def should_enter(ev: float, min_ev_threshold: float = 0.03) -> bool:
    """Only enter if EV exceeds threshold after fees"""
    return ev > min_ev_threshold
```

Minimum EV threshold of 3% is a starting point — calibrate based on your fill rate and slippage.

---

### Layer 5 — Kelly Sizing (`trading/kelly.py`)

```python
def fractional_kelly(
    model_prob: float,
    market_price: float,
    kelly_fraction: float = 0.25  # NEVER use 1.0 on short-duration markets
) -> float:
    """
    Returns fraction of bankroll to wager.
    p = probability of win (model_prob)
    q = probability of loss (1 - model_prob)
    b = net payout per $1 risked = (1 - market_price) / market_price
    f* = (p*b - q) / b
    """
    p = model_prob
    q = 1 - p
    b = (1 - market_price) / market_price  # net odds

    full_kelly = (p * b - q) / b
    full_kelly = max(0, full_kelly)  # no negative bets

    return full_kelly * kelly_fraction
```

**Hard rule from practitioner doc:** NEVER full Kelly on short-duration (under 24h) markets. Use ¼ Kelly (0.25) as default. Can adjust to ½ Kelly (0.5) for high-conviction, longer-duration contracts only.

---

### Layer 6 — Risk Gate (`trading/risk.py`)

```python
class RiskManager:
    def __init__(self, bankroll: float, max_daily_loss_pct: float = 0.05,
                 max_position_pct: float = 0.10):
        self.bankroll = bankroll
        self.max_daily_loss = bankroll * max_daily_loss_pct  # 5% daily loss limit
        self.max_position = bankroll * max_position_pct      # 10% max per contract
        self.daily_pnl = 0.0
        self.open_positions = {}

    def can_trade(self, contract_id: str, position_size: float) -> tuple[bool, str]:
        if self.daily_pnl <= -self.max_daily_loss:
            return False, "Daily loss limit hit — bot halted for today"
        if position_size > self.max_position:
            return False, f"Position size ${position_size:.0f} exceeds max ${self.max_position:.0f}"
        return True, "ok"
```

Parameters to tune:
- `max_daily_loss_pct`: start at 5%, tighten once you have fill data
- `max_position_pct`: 10% per contract — never go above this
- Add max open positions (e.g. max 5 concurrent contracts)

---

### Layer 7 — Executor (`trading/executor.py`)

**This is your bottleneck — 604ms mean latency per the reference architecture doc.**

Polymarket CLOB API:
- Base URL: `https://clob.polymarket.com`
- Auth: ECDSA signature with your Polygon wallet private key
- Key endpoints:
  - `GET /markets` — list open markets
  - `POST /order` — place limit/market order
  - `GET /orders` — check order status
  - `DELETE /order/{id}` — cancel order

```python
import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

class CLOBExecutor:
    BASE_URL = "https://clob.polymarket.com"

    def __init__(self, private_key: str):
        self.account = Account.from_key(private_key)
        self.client = httpx.AsyncClient(timeout=10.0)

    async def place_order(self, token_id: str, side: str,
                          size: float, price: float) -> dict:
        order = {
            "token_id": token_id,
            "side": side,       # "BUY" or "SELL"
            "size": str(size),
            "price": str(price),
            "type": "LIMIT",
        }
        # Sign and submit — refer to Polymarket CLOB API docs for exact signing spec
        # Use py-clob-client library: pip install py-clob-client
        ...
```

**Use `py-clob-client`** (Polymarket's official Python client) — don't roll your own signing logic.

Optimisation priorities for latency:
1. Use `httpx.AsyncClient` with connection pooling (not `requests`)
2. Keep a persistent WebSocket connection — don't reconnect per order
3. Pre-compute and cache signatures where possible
4. Co-locate if serious: Polymarket's infrastructure is US-based

---

### Layer 8 — Tracking (`tracking/`)

```python
# tracking/pnl.py
import json
from datetime import datetime
from pathlib import Path

class PnLTracker:
    def __init__(self, log_file: str = "fills.jsonl"):
        self.log_file = Path(log_file)

    def log_fill(self, contract_id: str, side: str, size: float,
                 price: float, model_prob: float, market_prob: float):
        record = {
            "ts": datetime.utcnow().isoformat(),
            "contract": contract_id,
            "side": side,
            "size": size,
            "price": price,
            "model_prob": model_prob,
            "market_prob": market_prob,
            "edge": model_prob - market_prob,
        }
        with self.log_file.open("a") as f:
            f.write(json.dumps(record) + "\n")
```

Log every fill with your model probability at entry time. This lets you:
- Track realised vs expected EV
- Calibrate your probability model over time
- Detect if your edge is decaying

---

## Configuration (`config.py`)

```python
# Risk parameters
BANKROLL_USDC = 500          # Starting capital in USDC
MAX_DAILY_LOSS_PCT = 0.05    # 5% daily hard stop
MAX_POSITION_PCT = 0.10      # 10% max per contract
MAX_OPEN_POSITIONS = 5       # Max concurrent positions
KELLY_FRACTION = 0.25        # ¼ Kelly — do not increase initially

# EV parameters
MIN_EV_THRESHOLD = 0.03      # Minimum 3% EV after fees to enter
POLYMARKET_FEE = 0.02        # Verify current taker fee
MIN_MARKET_LIQUIDITY = 10000 # Minimum $10k market size

# Feed polling intervals (seconds)
FEDWATCH_POLL_INTERVAL = 60
ONCHAIN_POLL_INTERVAL = 300

# Deribit
DERIBIT_WS_URL = "wss://www.deribit.com/ws/api/v2"

# Polymarket
POLYMARKET_CLOB_URL = "https://clob.polymarket.com"
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
```

---

## Dependencies (`requirements.txt`)

```
py-clob-client          # Polymarket official Python client
websockets              # WebSocket connections (Deribit feed)
httpx                   # Async HTTP (faster than requests)
numpy                   # Log-space Bayesian math
eth-account             # Polygon wallet signing
python-dotenv           # .env loading
asyncio                 # Async event loop
```

---

## Build Order

Build and test one layer at a time in this sequence:

1. **Deribit DVOL feed** — get the WebSocket stream running, print DVOL values
2. **Polymarket CLOB monitor** — connect, list crypto/finance markets, track prices
3. **Bayesian engine** — unit test with synthetic signals, verify log-space stability
4. **Probability module** — convert DVOL to contract probability, compare to market
5. **EV gate** — filter for positive EV opportunities, log them (don't trade yet)
6. **Kelly + Risk** — size positions correctly, enforce limits
7. **Executor** — place first real order (start with minimum size)
8. **P&L tracker** — log all fills, verify reconciliation
9. **KL scanner** — add cross-contract mispricing detection last

**Do not skip the logging step in phase 5.** Run the bot in paper-trade mode (log signals, don't execute) for at least a few days before live trading. Verify your model probability is directionally correct before risking capital.

---

## Key Risks to Handle

| Risk | Mitigation |
|---|---|
| Polymarket API downtime | Catch exceptions, halt gracefully, alert via log |
| Deribit WebSocket drop | Auto-reconnect with exponential backoff |
| Model probability systematically wrong | Paper trade first, track calibration |
| Gas/USDC bridging delays | Pre-fund Polygon wallet before starting |
| Resolution dispute on Polymarket | Avoid contracts with ambiguous resolution criteria |
| Full Kelly blowup | Hard-coded ¼ Kelly, no config override |

---

## First Command to Run in Claude Code

```bash
mkdir polymarket-bot && cd polymarket-bot
python -m venv venv && source venv/bin/activate
pip install websockets httpx numpy eth-account python-dotenv py-clob-client
touch main.py config.py .env requirements.txt
mkdir feeds engine market trading tracking utils
```

Then start with `feeds/deribit.py` — get DVOL streaming before anything else.

---

*Brief generated from design session — Claude.ai · March 2026*
