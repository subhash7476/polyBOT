# Polymarket Market Maker Bot — Design Spec

**Date:** 2026-03-31
**Status:** Approved
**Approach:** Actor-based pipeline (Approach B)

---

## Decisions

| Decision | Choice |
|---|---|
| Repo structure | Same repo, `maker/` package |
| Bankroll | Paper first, decide later |
| Target categories | Sports first, expand later |
| Fair value | Hybrid — market mid anchor + Bayesian skew |
| Adverse selection defense | Inventory-driven retreat + cancel-all circuit breaker |
| Latency model | Event-driven fills (500ms), polling repricing (1-2s) |
| Architecture | Actor-based pipeline with asyncio.Queue communication |

---

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                    maker/runner.py (entrypoint)                   │
│              python main.py --mode maker                         │
└──────────────────────┬───────────────────────────────────────────┘
                       │ spawns all actors via asyncio.gather()
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Shared: MakerState                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │InventoryBook│  │ OrderTracker │  │ QuoteIntent cache     │  │
│  │per-market    │  │ order_id →   │  │ market → (bid, ask,   │  │
│  │net position  │  │ status,side, │  │  size, last_sent_at)  │  │
│  │+ skew factor │  │ market,price │  │                       │  │
│  └─────────────┘  └──────────────┘  └───────────────────────┘  │
│  + AppState (reused from taker — feeds, markets, balance)       │
└─────────────────────────────────────────────────────────────────┘
          │            │             │            │           │
          ▼            ▼             ▼            ▼           ▼
   ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ ┌─────────┐
   │  Market   │ │  Quote   │ │  Order   │ │  Fill   │ │Inventory│
   │ Selector  │ │  Engine  │ │ Manager  │ │ Poller  │ │ Manager │
   │  (15min)  │ │  (1-2s)  │ │ (event)  │ │ (500ms) │ │(on fill)│
   └───────────┘ └──────────┘ └──────────┘ └─────────┘ └─────────┘
```

### Queue Communication

| From | To | Queue | Payload |
|---|---|---|---|
| MarketSelector | QuoteEngine | `active_markets_q` | `set[str]` of token_ids to quote |
| QuoteEngine | OrderManager | `quote_intents_q` | `QuoteIntent(token_id, bid_price, ask_price, bid_size, ask_size)` |
| FillPoller | InventoryManager | `fills_q` | `Fill(token_id, side, price, size, order_id)` |
| InventoryManager | QuoteEngine | `skew_updates_q` | `SkewUpdate(token_id, skew_factor)` |
| CircuitBreaker | OrderManager | `cancel_q` | `CancelAll(token_id)` or `CancelAll("*")` |

MakerState is shared state read by all actors under lock. Queues carry events/commands; state carries the latest snapshot.

---

## Actor Specifications

### 1. MarketSelector

**Cadence:** Every 15 minutes (aligned with CLOBMonitor rediscovery).

**Selection criteria:**

| Parameter | Value | Rationale |
|---|---|---|
| `MIN_DAILY_VOLUME` | $100 | Below this, quotes never fill |
| `MAX_ACTIVE_MARKETS` | 20 | Capital spread limit |
| `MIN_SPREAD` | 0.04 (4c) | No point quoting if pro MMs already at 1-2c |
| `MAX_TIME_TO_RESOLUTION` | 7 days | Long-dated markets tie up inventory |
| `CATEGORY` | "sports" | Phase 1 only, config-driven later |

**Ranking:** Markets passing filters ranked by `spread x volume_24h`. Widest spread on most liquid markets are the best quoting opportunities.

**Output:** Pushes `set[str]` of token_ids to `active_markets_q`. When a market drops out, QuoteEngine stops quoting and OrderManager cancels outstanding orders.

**Contract parsing:** Reuses `parse_contract()`. Needs new sports detection regex (team names, "win", "defeat", league names) since sports currently fall into "event" category.

---

### 2. QuoteEngine

**Cadence:** Every 1.5 seconds.

**Fair value computation (hybrid):**

```
fair_value = market_mid + inventory_adjustment + model_adjustment

inventory_adjustment: clamped to [-0.03, +0.03]
  positive = holding YES → push fair value up to make ask attractive

model_adjustment: 0.0 initially
  enabled only when calibration confidence exceeds threshold
  uses BayesianEngine probability as skew direction
```

**Spread computation:**

| Factor | Effect |
|---|---|
| Base spread | 0.06 (3c each side) |
| Low volume (< $500/day) | +0.02 |
| Inventory penalty | +0.01 per $1 net exposure |
| Near resolution (< 48h) | +0.03 |
| Very near resolution (< 6h) | Widen to MAX_SPREAD (0.15) — effectively pull quotes |

**Spread bounds:** MIN_SPREAD = 0.04, MAX_SPREAD = 0.15.

**Quote size:** $10 USDC per side per market (conservative for paper mode).

**Stale quote detection:** Only emits QuoteIntent if price changed by > 1 tick (0.01) versus cached last-sent intent. Prevents unnecessary order churn.

---

### 3. OrderManager

**Cadence:** Event-driven — reads from `quote_intents_q` and `cancel_q`.

**Order strategy:**

| Decision | Choice | Reason |
|---|---|---|
| Cancel-then-place | No amend API in py-clob-client | API limitation |
| Post-only on all orders | Never cross the spread as taker | Core to maker economics |
| GTC (no expiry) | QuoteEngine drives lifecycle | Simpler than GTD |
| One bid + one ask per market | Phase 1 simplicity | Add depth levels later |
| Batch cancel | `cancel_orders([id1, id2])` | Fewer API round-trips |

**Rate limiting:** 20 active markets repricing every 1.5s = worst case 40 orders/cycle (~27/sec). Mitigated by: batch cancels, stale-quote detection reducing actual reprices to ~5-10/cycle, staggered placement across the 1.5s window.

**Paper mode:** Logs quote intents without hitting the API. FillPoller's paper simulation generates synthetic fills when book crosses our quotes.

---

### 4. FillPoller

**Cadence:** Every 500ms — fastest loop, directly tied to adverse selection defense.

**Live mode:** Batch polls `clob.get_orders()` each cycle. Compares order states against cached `_order_states`. When an order transitions from OPEN to MATCHED, emits a `Fill` to `fills_q`.

**Paper mode:** Each cycle, checks if market's best_ask <= our bid_price (our bid fills) or market's best_bid >= our ask_price (our ask fills). Generates synthetic Fill events.

**Why polling:** py-clob-client has no WebSocket channel for per-user fill notifications. The CLOB WebSocket only sends book updates. `get_orders()` polling at 500ms with batched requests is one API call per cycle.

---

### 5. InventoryManager + CircuitBreaker

**Cadence:** Event-driven — processes fills from `fills_q`.

**Inventory tracking:**

```
positions: dict[str, float]  # token_id → net_shares
  positive = holding YES
  negative = holding NO

skew_factor = positions[token_id] / MAX_INVENTORY_PER_MARKET
  clamped to [-1.0, +1.0]
  pushed to QuoteEngine via skew_updates_q
```

**Limits:**

| Parameter | Value |
|---|---|
| `MAX_INVENTORY_PER_MARKET` | $50 |
| `MAX_TOTAL_INVENTORY` | $200 across all markets |

**Circuit breaker triggers:**

| Trigger | Action | Cooldown |
|---|---|---|
| Per-market inventory hits $50 cap | Cancel all quotes for that market | 5 minutes |
| Total inventory hits $200 cap | Cancel ALL quotes | 5 minutes |
| Rapid double-fill (both sides within 5s) | Cancel quotes for that market | 5 minutes |
| Daily P&L loss > 3% of bankroll | Cancel ALL quotes | Rest of day |

**Resolution:** When a market resolves, settles the position using same math as taker's `resolve_position()`. Reuses `PositionLedger` for persistence.

---

## Entrypoint & Module Wiring

**Launch:**

```bash
python main.py              # taker (default, existing behavior)
python main.py --mode maker # maker bot
```

**Reuse map:**

| Module | Reuse | Modification |
|---|---|---|
| `market/clob_monitor.py` | As-is | Book updates for all markets |
| `market/state.py` (AppState) | As-is | Feeds + market state |
| `feeds/microstructure.py` | As-is | Spot prices for model skew |
| `feeds/deribit.py` | As-is | DVOL for spread widening |
| `engine/contract_parser.py` | Small add | Sports detection regex |
| `engine/bayesian.py` | As-is | Fair value skew (Phase 2+) |
| `trading/positions.py` | As-is | PositionLedger for maker fills |
| `calibration/tracker.py` | Small add | `log_maker_fill()` method |
| `calibration/metrics.py` | Small add | Maker-specific metrics |
| `trading/balance.py` | As-is | USDC polling |
| `dashboard/` | Adapt | Maker view (live quotes, inventory) |
| `telegram_bot.py` | Adapt | `/maker_status`, `/inventory` commands |

**NOT reused:**

| Module | Reason |
|---|---|
| `trading/risk.py` | Replaced by InventoryManager |
| `trading/executor.py` | Replaced by OrderManager |
| `feeds/weather.py` | No weather markets |
| `feeds/macro.py` | No rates/macro markets |
| `feeds/onchain.py` | No crypto markets in Phase 1 |

**New files:**

```
maker/
├── __init__.py
├── runner.py              # entrypoint, wiring
├── state.py               # MakerState dataclass
├── market_selector.py     # MarketSelector actor
├── quote_engine.py        # QuoteEngine actor
├── order_manager.py       # OrderManager actor
├── fill_poller.py         # FillPoller actor
├── inventory.py           # InventoryManager + CircuitBreaker
└── types.py               # QuoteIntent, Fill, SkewUpdate, CancelAll
```

---

## Paper Mode, Calibration & Go/No-Go

### Maker-Specific Metrics

| Metric | Description |
|---|---|
| Spread capture | ask_fill - bid_fill on round-trips |
| Fill rate | total_fills / total_quotes_posted |
| Adverse selection rate | % of fills where market moved against us within 60s |
| Inventory turnover | Average time to round-trip (buy then sell on same market) |
| Net P&L | Realized (round-trips) + unrealized (open inventory marked to mid) |

### 21-Day Paper Test

| Phase | Days | Gate |
|---|---|---|
| Plumbing | 1-7 | Quotes posting correctly, fills detected within 1s, cancel-all works, no orphaned orders |
| Spread capture | 8-14 | >= 50 round-trips. Mean spread capture > 3c. Fill rate 10-30%. |
| Edge validation | 15-21 | Net simulated P&L positive. Adverse selection < 40%. No day worse than -3%. Inventory turnover avg < 4h. |

### Continue Conditions (all must be true)

1. >= 100 total fills (both sides combined)
2. >= 30 round-trip completions (bid fill + ask fill on same market)
3. Mean spread capture > 3c per round-trip
4. Adverse selection rate < 40%
5. Fill rate between 10-30% of posted quotes
6. No daily simulated loss > 3% of bankroll
7. Inventory hold time median < 4 hours

### Kill Conditions (any one sufficient)

1. Fill rate < 5% after 14 days — quotes too wide, nobody crosses
2. Adverse selection > 50% — getting picked off more than half the time
3. Mean spread capture < 1c — not enough edge to cover eventual live fees
4. Inventory hold time median > 24 hours — stuck holding, markets too thin
5. < 50 fills by day 14 — Sports volume too low for this strategy
6. Circuit breaker fires > 3x per day average — market too dangerous

### Validation

Bootstrap CI from taker bot (`bootstrap_win_rate_ci()`) applies here: compute CI on proportion of fills with spread capture > 0. If lower bound > 50%, spread capture is real.

---

## Build Phases (from architecture diagram)

| Phase | Weeks | Components | Deliverable |
|---|---|---|---|
| 1 | 1-2 | OrderManager, FillPoller, types.py, MakerState | Can place/cancel/detect fills on live CLOB |
| 2 | 3-4 | QuoteEngine, MarketSelector, sports regex | Automated quoting on selected sports markets |
| 3 | 5-6 | InventoryManager, CircuitBreaker, skew loop | Defense layer operational, paper test begins |
| 4 | 7-8 | Maker metrics, dashboard adapter, go/no-go | Full paper test, data-driven live/kill decision |

---

## Evidence Base

This design is informed by [Prediction Market Microstructure](https://www.jbecker.dev/research/prediction-market-microstructure) (J. Becker), which documents:

- Makers earn +1.12% excess return vs takers at -1.12% on prediction markets
- Sports markets show 2.23pp maker-taker gap (target category)
- World Events show 7.32pp gap (expansion category)
- Edge comes from exploiting biased order flow (YES longshot preference), not superior forecasting
- Professional makers entered post-2024 election, flipping the taker-maker gap

### py-clob-client Capabilities (confirmed)

| Feature | Supported | Critical for MM |
|---|---|---|
| GTC limit orders | Yes | Yes — persistent quotes |
| Post-only mode | Yes | Yes — never pay taker fees |
| Batch cancel | Yes | Yes — emergency defense |
| cancel_all() | Yes | Yes — circuit breaker |
| get_orders() polling | Yes | Yes — fill detection |
| WebSocket fill notifications | No | Must poll instead |
| Order amend/modify | No | Must cancel-and-replace |
