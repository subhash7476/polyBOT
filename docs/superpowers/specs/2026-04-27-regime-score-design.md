# Unified Regime Score System — Design Spec
*Date: 2026-04-27 | Status: Approved*

---

## Overview

Add a `RegimeDecision` system to the maker bot that continuously evaluates market conditions
(informed order flow, adverse selection, inventory depth, time pressure) and returns a single
structured object driving spread width, quote size, and fair-value skew — replacing ad-hoc
per-signal branching with a single, testable decision point.

Scope: maker pipeline only. UMA tail-end trader is a separate spec.

---

## New Files

| File | Purpose |
|---|---|
| `maker/regime.py` | `RegimeDecision` dataclass + `compute_regime_score()` pure function + `CATEGORY_SPREAD_MULTIPLIER` table |
| `maker/vpin_poller.py` | Async coroutine polling CLOB `/trades` per active market, writes VPIN into `AppState` |

## Modified Files

| File | Change |
|---|---|
| `market/state.py` | Add `vpin`, `vpin_updated_at` to `ContractState` |
| `maker/markout_tracker.py` | Add per-market and per-category rolling stats + `get_markout_30s()` |
| `maker/inventory.py` | Add `get_inv_signed_pct(token_id)` read-only method |
| `maker/quote_engine.py` | Single `compute_regime_score()` call per market per cycle; apply all four outputs |
| `maker/runner.py` | Start `VPINPoller` as an isolated `asyncio.create_task()` |

---

## Section 1: Data Flow

```
VPINPoller  ──poll /trades every 30s──→  AppState.markets[token].vpin
MarkoutTracker ──per fill──────────────→ by_market[token] / by_category[cat]

QuoteEngine (per market, per cycle):
  regime = compute_regime_score(cs.vpin, markout_30s, inv_signed, hours, category)
  fair_value  = cs.mid + regime.skew_adjustment          ← skew applied FIRST
  base_spread = compute_spread(abs_inventory, hours)
  spread      = clamp(base_spread * cat_mult * regime.spread_multiplier,
                      MIN_SPREAD, MAX_SPREAD)
  bid, ask    = fair_value ∓ spread/2
  size        = max(BASE_QUOTE_SIZE * regime.size_multiplier, MIN_QUOTE_SIZE)
```

`VPINPoller` writes directly into `AppState` under the existing market lock — same pattern as `CLOBMonitor`. No new queues.

---

## Section 2: `RegimeDecision` and Scoring

### Dataclass

```python
@dataclass
class RegimeDecision:
    score: float              # 0.0–1.0 composite intensity
    spread_multiplier: float  # 1.0–2.5x regime factor (category baseline applied separately)
    size_multiplier: float    # 0.25–1.0x
    skew_adjustment: float    # ±0–0.02, applied to fair_value before spread derivation
    flags: frozenset[str]     # {"defensive", "unwind", "extreme", "toxic_flow"}
```

### Signature

```python
def compute_regime_score(
    vpin: float,                # 0.0–1.0; 0.5 = neutral/stale default
    markout_30s: float,         # rolling avg T+30s; negative = adverse selection
    inv_signed: float,          # signed fraction of cap, –1.0 to +1.0
    hours_to_resolution: float, # pre-computed, clamped [0, 168]
    category: str,
) -> RegimeDecision:
```

### Score Components

| Component | Formula | Weight | Rationale |
|---|---|---|---|
| VPIN | `abs(vpin − 0.5) / 0.3`, clamped 0–1 | 0.35 | Imbalance magnitude; symmetric around 0.5 |
| Markout | `clamp(−markout_30s / 0.05, 0, 1)` | 0.25 | Observed adverse selection; /0.05 reduces noise sensitivity |
| Inventory | `abs(inv_signed)` | 0.25 | Continuous linear urgency from 0 to cap |
| Time | `clamp((24 − hours) / 24, 0, 1) ** 1.5` | 0.15 | Non-linear; accelerates in final hours |

```python
score = (0.35 * vpin_score + 0.25 * markout_score +
         0.25 * inv_score  + 0.15 * time_score)
# clipped to [0.0, 1.0]
```

### Output Derivation

```python
spread_multiplier = 1.0 + score * 1.5              # range: 1.0x–2.5x
size_multiplier   = max(0.25, 1.0 - 0.75 * score)  # range: 1.0x–0.25x
skew_adjustment   = -sign(inv_signed) * min(0.02, 0.02 * abs(inv_signed) * (0.5 + score))
```

Skew nudges `fair_value` toward inventory reduction. Small inventory → small skew. High score → stronger push. Cap ±2¢.

### Flags

| Flag | Condition |
|---|---|
| `"defensive"` | `score > 0.4` |
| `"unwind"` | `abs(inv_signed) > 0.6` AND (`markout_30s < −0.01` OR `hours_to_resolution < 4`) |
| `"extreme"` | `score > 0.8` |
| `"toxic_flow"` | `vpin_score > 0.7` |

### Category Spread Baseline (seeded; tune empirically after 500+ fills/category)

```python
CATEGORY_SPREAD_MULTIPLIER: dict[str, float] = {
    "finance":       1.0,
    "crypto":        1.2,
    "politics":      1.3,
    "sports":        1.6,
    "weather":       2.0,
    "entertainment": 2.2,
    "default":       1.5,
}
```

Applied in `QuoteEngine` separately: `spread *= CATEGORY_SPREAD_MULTIPLIER.get(cs.category, 1.5)`.
Keeps the static category baseline decoupled from the dynamic regime component.

---

## Section 3: VPINPoller

### Algorithm

```
Every 30s per active market token:
  1. GET /trades?token_id=X&limit=100 (CLOB REST API)
  2. Filter to trades newer than last_trade_id (incremental — no reprocessing)
  3. Classify each trade via tick rule:
       price > prev_price → BUY
       price < prev_price → SELL
       price == prev_price → carry forward last classification
  4. Append to per-token deque(maxlen=100) rolling buffer
  5. Guard: if num_trades < 10 or total_volume == 0 → raw_vpin = 0.5
  6. Compute size-weighted VPIN:
       raw_vpin = abs(Σbuy_size − Σsell_size) / (Σbuy_size + Σsell_size)
  7. EMA smooth: vpin = 0.7 * prev_vpin + 0.3 * raw_vpin
  8. Staleness guard: if now − vpin_updated_at > 180s → vpin = 0.5
  9. Acquire market lock → write AppState.markets[token_id].vpin = vpin
                                                    .vpin_updated_at = now
```

**Why size-weighted:** large trades carry more information; volume-weighting reduces noise from many tiny orders.

**Why EMA:** prevents regime oscillation from single-cycle VPIN spikes.

**Why stale = 0.5 (not 0.0):** `vpin_score = abs(vpin − 0.5) / 0.3`. Setting vpin=0 would compute as maximum toxicity (vpin_score = 1.0). Stale data must be neutral, not alarming.

### Failure Isolation

- Per-token `try/except`: one bad token does not affect others
- Top-level task `try/except` with restart: poller death does not crash main loop
- On any failure, `ContractState.vpin` stays at last value (or 0.5 on first failure)
- Heartbeat log every 50 cycles to confirm liveness

### Polling Rate

Baseline 30s. Future optimization: 20s if market has active inventory or open quotes, 60s otherwise.

---

## Section 4: MarkoutTracker Changes

### New State

```python
by_market: dict[str, RollingStats]    # keyed by token_id
by_category: dict[str, RollingStats]  # keyed by category string
```

`RollingStats` tracks EMA `avg_30s`, sample count `n`, and last update.

### Fallback Method

```python
MIN_MARKET_FILLS = 20
MIN_CATEGORY_FILLS = 100

def get_markout_30s(self, token_id: str, category: str) -> float:
    per_market = self.by_market.get(token_id)
    if per_market and per_market.n >= MIN_MARKET_FILLS:
        return per_market.avg_30s
    per_cat = self.by_category.get(category)
    if per_cat and per_cat.n >= MIN_CATEGORY_FILLS:
        return per_cat.avg_30s
    return 0.0   # neutral — no data yet
```

Three-tier fallback: per-market → per-category → neutral zero. Prevents noisy inputs early in deployment.
Existing aggregate stats and dashboard reporting are unchanged.

---

## Section 5: QuoteEngine Integration

### `market/state.py` additions

```python
# ContractState — add to existing dataclass
vpin: float = 0.5
vpin_updated_at: float = 0.0
```

Add `hours_to_resolution` as a `@property` on `ContractState`:
```python
@property
def hours_to_resolution(self) -> float:
    if not self.end_date_iso:
        return 48.0
    from datetime import datetime, timezone
    end = datetime.fromisoformat(self.end_date_iso.replace("Z", "+00:00"))
    hours = (end - datetime.now(timezone.utc)).total_seconds() / 3600
    return max(0.0, min(168.0, hours))
```

### `maker/inventory.py` addition

```python
def get_inv_signed_pct(self, token_id: str) -> float:
    inv = self._inventory.get(token_id, 0.0)
    cap = self._cap
    return max(-1.0, min(1.0, inv / cap)) if cap > 0 else 0.0
```

Cap must be consistent (global or per-market) — same value used here and in `InventoryManager` limit checks.

### `maker/quote_engine.py` — per-market pricing loop

```python
# Guard: uninitialized/stale book (defaults: best_bid=0.0, best_ask=1.0)
if cs.best_bid == 0.0 and cs.best_ask == 1.0:
    fair_value = 0.5   # neutral until book is populated
else:
    fair_value = cs.mid

# Single regime call — result reused for ALL adjustments
regime = compute_regime_score(
    vpin                = cs.vpin,
    markout_30s         = markout_tracker.get_markout_30s(token_id, cs.category),
    inv_signed          = inv_manager.get_inv_signed_pct(token_id),
    hours_to_resolution = max(0.0, cs.hours_to_resolution),
    category            = cs.category,
)

# 1. Apply skew BEFORE spread derivation
fair_value += regime.skew_adjustment

# 2. Base spread from existing compute_spread()
base_spread = compute_spread(abs_inventory=abs(inv_signed), hours_to_expiry=cs.hours_to_resolution)

# 3. Category baseline × regime dynamic multiplier, then clamp
cat_mult = CATEGORY_SPREAD_MULTIPLIER.get(cs.category, 1.5)
spread   = clamp(base_spread * cat_mult * regime.spread_multiplier, MIN_SPREAD, MAX_SPREAD)

# 4. Bid/ask
bid = fair_value - spread / 2
ask = fair_value + spread / 2

# 5. Size with floor
size = max(BASE_QUOTE_SIZE * regime.size_multiplier, MIN_QUOTE_SIZE)

# 6. Per-market regime debug log (every cycle — critical for tuning)
inv_signed_val = inv_manager.get_inv_signed_pct(token_id)
markout_val    = markout_tracker.get_markout_30s(token_id, cs.category)
log.debug(
    f"regime[{token_id[:8]}] score={regime.score:.3f} "
    f"vpin={cs.vpin:.3f} markout={markout_val:.4f} "
    f"inv={inv_signed_val:.2f} hours={cs.hours_to_resolution:.1f} "
    f"spread_x={regime.spread_multiplier:.2f} size_x={regime.size_multiplier:.2f} "
    f"skew={regime.skew_adjustment:+.4f} flags={','.join(sorted(regime.flags)) or 'none'}"
)
if "extreme" in regime.flags:
    log.warning(f"extreme regime [{token_id[:8]}] score={regime.score:.2f}")
if "toxic_flow" in regime.flags:
    log.info(f"toxic_flow [{token_id[:8]}] vpin={cs.vpin:.2f}")
```

`compute_regime_score()` is called **exactly once per market per cycle**. The `regime` object is reused for all four outputs. No recomputation in branches.

### `maker/runner.py`

```python
asyncio.create_task(vpin_poller.run(state, clob_client))
```

Top-level `try/except` in `vpin_poller.run()` prevents crash propagation.

---

## Section 6: Testing

### `maker/regime.py` — pure function, no mocks needed

- Neutral inputs (`vpin=0.5, markout=0, inv=0, hours=48`) → `score ≈ 0`, all multipliers ≈ 1.0, no flags
- `vpin=0.85` → `vpin_score` near 1.0, `"toxic_flow"` flag set
- `markout_30s=−0.06` → `markout_score=1.0`, `"defensive"` flag
- `inv_signed=0.8, hours=2` → `"unwind"` flag, negative `skew_adjustment`
- Property: `spread_multiplier` ∈ `[1.0, 2.5]` across 1000 random inputs
- Property: `skew_adjustment` sign always opposes `inv_signed` sign
- All four flags tested at exact boundary values

### `maker/vpin_poller.py` — mocked trade lists

- 3 buys × 10 shares, 1 sell × 5 shares → `raw_vpin = (30−5)/35 ≈ 0.714`
- < 10 trades → returns 0.5 (guard)
- EMA: `prev=0.5, raw=0.9` → `smoothed = 0.7×0.5 + 0.3×0.9 = 0.62`
- `last_update_ts` 200s ago → `vpin` resets to 0.5
- Incremental: trades with already-seen IDs are skipped

### `maker/markout_tracker.py` — fallback chain

- Per-market 25 fills → returns per-market avg
- Per-market 15 fills, category 120 fills → returns category avg
- No data → returns 0.0

### `maker/quote_engine.py` — behavioral

- High-VPIN market: `spread > base_spread`, `size < BASE_QUOTE_SIZE`
- Negative markout: `"defensive"` in `regime.flags`
- Long inventory (`inv_signed=0.7`): `skew_adjustment < 0`
- `compute_regime_score()` called exactly once per market (mock with call counter)
- `spread` always within `[MIN_SPREAD, MAX_SPREAD]` at extreme inputs

### Regression

Neutral regime (all default inputs) → `spread_multiplier=1.0`, `size_multiplier=1.0`, `skew_adjustment=0.0`.
Existing `test_quote_engine.py` tests pass unchanged.

---

## Constants to Define

| Constant | Location | Suggested Value |
|---|---|---|
| `MIN_SPREAD` | `maker/regime.py` | 0.005 (0.5¢) |
| `MAX_SPREAD` | `maker/regime.py` | 0.15 (15¢) |
| `MIN_QUOTE_SIZE` | `maker/quote_engine.py` | 5 shares |
| `MIN_VPIN_TRADES` | `maker/vpin_poller.py` | 10 |
| `VPIN_STALE_SECONDS` | `maker/vpin_poller.py` | 180 |
| `MIN_MARKET_FILLS` | `maker/markout_tracker.py` | 20 |
| `MIN_CATEGORY_FILLS` | `maker/markout_tracker.py` | 100 |

---

## Out of Scope (this spec)

- UMA tail-end trader (separate spec)
- Dashboard visualization of regime score / VPIN per market
- Empirical re-calibration of `CATEGORY_SPREAD_MULTIPLIER` (post 500+ fills/category)
- VPIN trend signal (extend after baseline validated)
- Quote suppression at extreme regime (option B, rejected for initial deploy)
