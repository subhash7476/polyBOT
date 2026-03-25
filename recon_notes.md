# Reconnaissance Notes — Reference Repo Patterns

**Date:** 2026-03-25
**Repos Surveyed:**
1. `polymarket-mcp-server` (caiovicentino) — MCP server with 45 tools, safety guardrails
2. `polymarket-assistant-tool` (FiatFiorino) — Binance-Polymarket order flow fusion
3. `polymarket-agents` (Polymarket official) — Gamma API + LLM probability estimation

---

## What's Worth Stealing (Patterns, Not Code)

### 1. Multi-Layer Safety Guardrail Architecture (from MCP Server)

**Pattern:** Pre-trade validation orchestration with three tiers:
- **Liquidity gate:** Reject orders in markets with minimum order size > threshold
- **Spread check:** Abort if bid-ask > 5% (prevents bad fills in thin order books)
- **Exposure bucketing:** Track by market/direction to cap correlated risk

**Why relevant:** Our `RiskManager` does group bucketing by direction, but doesn't implement spread checks. Adding a pre-execution liquidity gate would prevent fill slippage surprises.

**Integration point:** `trading/executor.py` — add spread_pct check in `CLOBExecutor.place_order()` before submitting. Skip orders in markets where `(ask - bid) / mid > 0.05`.

---

### 2. Order Flow Imbalance as Microstructure Signal (from FiatFiorino's Binance Fusion)

**Pattern:** Real-time bid/ask depth ratio as short-term directional signal
- Ratio > 2.5 (buy pressure) or < 0.4 (sell pressure) sustained over 3 readings = direction hint
- Timeframe: 5–30 seconds predictive half-life (very short-term)
- Not useful for pre-resolution (>1 hour out); strong for tactical entry/exit timing

**Why relevant:** Our engine does not consume order book depth (bid/ask levels). MicrostructureFeed adds funding rate + spot price, but not CLOB depth. Adding YES/NO order book depth would unlock tactical signals.

**Integration point:**
- `state/core.py` (ContractState): add `bid_depth_top5` and `ask_depth_top5` fields
- `feeds/clob_monitor.py`: populate from `/orderbook` endpoint (depth levels, not just BBO)
- New signal in `engine/orderbook_imbalance.py`: compute `(sum(ask_sizes) - sum(bid_sizes)) / total_size`

**Validation:** Order book imbalance predicts 5–30s direction, not resolution. Use for limit order placement only, not for longer-dated theses.

---

### 3. Modular Signal Abstraction with Pydantic Models (from Polymarket Agents)

**Pattern:** Standardized data model layer decouples data sourcing from signal logic
- Objects.py: Pydantic BaseModel for Trade, Market, Event (reusable across pipelines)
- Allows independent evolution of Gamma API calls, LLM prompts, and data-driven rules

**Why relevant:** Our engine mixes contract parsing, probability computation, and signal weighting in tightly coupled code. Extracting common data shapes to a models layer would simplify multi-signal composition.

**Integration point:**
- Create `engine/models.py` with `SignalReading(strength: float, confidence: float, source: str, lag_seconds: int)`
- Standardize all signal outputs to this shape (including new order book imbalance, flatline)
- `BayesianEngine.add_signal()` accepts `SignalReading` instead of raw tuples

---

### 4. WebSocket Real-Time Streaming with Exponential Backoff (from MCP Server)

**Pattern:** Resilient feed subscription with auto-reconnect
- Connection drop detected → exponential backoff (1s, 2s, 4s... up to 30s)
- No mock testing; always real API integration to catch network failures early

**Why relevant:** Our CLOBMonitor and MacroFeed use httpx blocking calls. MacroFeed falls back to curl on FRED TLS issues, but doesn't implement exponential backoff for sustained outages.

**Current state:** Adequate for now (feeds refresh every 5–60s). Consider if expanding to more WebSocket sources.

---

### 5. Gamma API as the Canonical Source for Market Metadata (from Polymarket Agents)

**Pattern:** All market discovery, event linking, and category routing through Gamma API, not inferred from CLOB prices
- Decouples market structure from price discovery
- Simplifies multi-token handling (YES/NO/Complement tokens)

**Why relevant:** Our `CLOBMonitor` discovers markets from Gamma API snapshot, then updates prices via WebSocket. Gamma remains source of truth for event_id, question_id, category, expiry. Good design already in place.

---

## What's Irrelevant for Current Stage

### 1. LLM Probability Generation (Polymarket Agents)
**Why skip:** We already have Bayesian multi-signal fusion + macro models (Poisson for rates, Normal for consensus). LLM probability estimation (sending prompts to Claude) is a post-validation enhancement—only useful after 20+ resolved markets to fine-tune category-specific rules.

**Deferral:** Stage 3 (post-calibration refinement).

---

### 2. MCP (Model Context Protocol) Server Wrapper (caiovicentino)
**Why skip:** MCP is a UI/LLM integration layer. Our bot is a standalone autonomous agent. MCP shines for human-in-the-loop trading (Claude at the keyboard), not 24/7 automation.

**Reuse point:** Safety guardrail logic is portable (spread checks, liquidity gates). Architecture (MCP tooling) is not.

---

### 3. Extensive Dashboard UI Components (Both)
**Why skip:** We output logs + calibration metrics. UI is useful for monitoring but not core to trading logic. Telegram bot covers alerts.

---

## Specific Integration Points into Our Codebase

### Priority 1: Order Book Depth Consumption (Week 1)

1. **ContractState extension** (`state/core.py`)
   ```python
   @dataclass
   class ContractState:
       # ... existing fields ...
       bid_depth_top5: list[tuple[float, float]] = field(default_factory=list)  # [(price, size), ...]
       ask_depth_top5: list[tuple[float, float]] = field(default_factory=list)
   ```

2. **CLOBMonitor enhancement** (`feeds/clob_monitor.py`)
   - When receiving book update, extract depth levels (not just BBO)
   - Store in `ContractState.bid_depth_top5`, `ask_depth_top5`

3. **New signal** (`engine/orderbook_imbalance.py`)
   ```python
   def compute_order_imbalance(state: ContractState) -> tuple[float, float]:
       """
       Returns (strength ∈ [-1, +1], confidence ∈ [0, 1])
       strength > 0 = buy pressure, < 0 = sell pressure
       """
       # (ask_size_sum - bid_size_sum) / total_size → normalized imbalance
       # confidence = max(0, 1 - age_seconds / 30)  ← 30s half-life
   ```

4. **Signal filter tolerance** (`engine/signal_filter.py`)
   - Order imbalance is tactical (5–30s), not strategic
   - Allow it as confirming signal, not primary

---

### Priority 2: Spread Gate in Executor (Week 1)

**Location:** `trading/executor.py::CLOBExecutor.place_order()`

```python
# Before submitting order:
spread_pct = (market_data.best_ask - market_data.best_bid) / market_data.mid_price
if spread_pct > 0.05:
    logger.warning(f"Spread {spread_pct:.2%} exceeds 5% gate in {market.id}, skipping")
    return ERROR
```

---

### Priority 3: Pydantic Models Refactor (Week 2)

**File:** `engine/models.py` (new)

```python
from pydantic import BaseModel
from enum import Enum

class SignalSource(str, Enum):
    DERIBIT_DVOL = "deribit_dvol"
    BINANCE_FUNDING = "binance_funding"
    MACRO_POISSON = "macro_poisson"
    FLATLINE = "flatline"
    ORDERBOOK_IMBALANCE = "orderbook_imbalance"
    VOLUME_DIVERGENCE = "volume_divergence"

class SignalReading(BaseModel):
    strength: float  # ∈ [-1, +1]
    confidence: float  # ∈ [0, 1]
    source: SignalSource
    lag_seconds: int  # age of underlying data
    metadata: dict = {}  # signal-specific context
```

Then refactor `BayesianEngine.add_signal()` to accept `SignalReading`.

---

### Priority 4: Exponential Backoff for Feed Failures (Week 2–3)

**Location:** Base feed class or `feeds/__init__.py`

Only implement if we add more WebSocket sources. Current polling+curl fallback is adequate.

---

## Risk & Validation Notes

### Flatline Detector (Pre-Launch Signal Hypothesis)
- **Hypothesis:** Markets with YES price range < 2% over 48h before resolution resolve to the leading side ~79–81%
- **Validation required:** 50+ resolved markets with this pattern
- **Concern:** May be biased by low-liquidity narrow markets; need p-value < 0.05 to validate

### Order Book Imbalance
- **Risk:** Very short half-life (30s); only useful for tactical fills
- **Not useful for:** Strategic theses >1 hour out (signal decays)
- **Validation:** First 50 trades; track if entry timing improved vs. mid-price baseline

### No Complement Arbitrage Yet
- **Blocker:** Our ContractState tracks YES token order book only
- **To enable:** Add `no_best_ask` from actual NO token CLOB feeds
- **Status:** Deferred to future phase

---

## Summary Table

| Pattern | Source | Worth Adopting? | Integration Point | Timeline |
|---------|--------|-----------------|-------------------|----------|
| Spread gate | MCP Server | **YES** | Executor | Week 1 |
| Order book depth | FiatFiorino | **YES** | CLOB Monitor + new signal | Week 1 |
| Imbalance signal | FiatFiorino | **YES** | engine/orderbook_imbalance.py | Week 1 |
| Pydantic models | Polymarket Agents | **YES** | Refactor BayesianEngine | Week 2 |
| Exponential backoff | MCP Server | LATER | Feed layer | Week 2–3 |
| LLM probability | Polymarket Agents | NO | Post-validation stage 3 | Q2 2026 |
| MCP wrapper | caiovicentino | NO | Not relevant (autonomous bot) | Never |
| Dashboard UI | Both | OPTIONAL | Monitoring layer | Q2 2026 |

