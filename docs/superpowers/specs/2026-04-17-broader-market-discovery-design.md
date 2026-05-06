# Broader Market Discovery — Design Spec

**Date:** 2026-04-17  
**Status:** Approved  
**Scope:** 3 line-changes across 2 files; no trading logic changes

---

## Problem

The bot currently quotes only 1 of 17 qualifying markets on Polymarket today. The remaining 16 pass every spread/volume/date filter in `filter_and_rank` but never enter the selection pool because of three compounding bugs in the discovery layer.

A live scan of all 49,828 Polymarket markets confirms exactly 17 pass the maker bot's selection criteria (spread ≥ 2¢, vol_24h ≥ $10k, bid 5–95¢, resolving 4h–7d). Examples: FURIA win IEM Rio ($107k/day, 3¢ spread), Alex Bolt vs Rio Noguchi tennis ($46k/day), Bitcoin $78k Apr 13–19 ($22k/day), multiple same-day weather markets.

---

## Root Causes

### Bug 1 — WebSocket frame size limit (clob_monitor.py:515)

`websockets.connect()` defaults to a 1 MB receive frame limit. When subscribing to 500+ markets, Polymarket sends initial order-book snapshots in frames that exceed 1 MB, causing `1009 (message too big)` disconnects. This is a Python library default, not a Polymarket server limit.

**Proof:** Live WS tests with 250–1000 real Polymarket token IDs confirm the server accepts any count. Failure only occurs client-side when `max_size` is not set.

### Bug 2 — Subscription cap too low (config.py:124)

`MAX_SUBSCRIBED_MARKETS=250` limits CLOBMonitor to 250 WS subscriptions. Markets ranked 251+ by the hybrid sort key (short-dated bonus + volume_24h + liquidity) never get seeded into `app_state.markets` and are invisible to `filter_and_rank`.

### Bug 3 — Volume filter mismatch in `_discover_and_seed` (market_selector.py:377)

The secondary seeding path in `MarketSelector._discover_and_seed` filters candidates using `meta.get("volume", 0)` — all-time CLOB volume. `filter_and_rank` correctly uses `cs.volume_24h if cs.volume_24h > 0 else cs.volume_usd`. A new esports or sports market with high 24h activity but low all-time CLOB volume fails the seed filter and never enters state, even as a fallback.

---

## Changes

### 1. `config.py` line 124 — raise default subscription cap

```python
# Before
MAX_SUBSCRIBED_MARKETS = int(os.getenv("MAX_SUBSCRIBED_MARKETS", "250"))

# After
MAX_SUBSCRIBED_MARKETS = int(os.getenv("MAX_SUBSCRIBED_MARKETS", "500"))
```

Existing `MAX_SUBSCRIBED_MARKETS` env-var override is fully backward-compatible: operators running with an explicit `MAX_SUBSCRIBED_MARKETS=250` are unaffected.

### 2. `market/clob_monitor.py` line 515 — allow larger WS frames

```python
# Before
async with websockets.connect(POLYMARKET_WS_URL, ping_interval=20) as ws:

# After
async with websockets.connect(POLYMARKET_WS_URL, ping_interval=20,
                               max_size=10 * 1024 * 1024) as ws:
```

10 MB is well within available system memory. At 500 markets the largest observed snapshot frame was ~3–4 MB. 10 MB provides 2.5× headroom.

### 3. `maker/market_selector.py` line 377 — align volume filter with filter_and_rank

```python
# Before
if meta.get("volume", 0) < _MIN_DAILY_VOLUME:

# After
if (meta.get("volume_24h", 0) or meta.get("volume", 0)) < _MIN_DAILY_VOLUME:
```

Mirrors the identical pattern already used in `filter_and_rank` (line 239) and in `_discover_and_seed`'s own ranking (line 400). Eliminates the inconsistency.

---

## Data Flow After Fix

```
CLOBMonitor (every 15 min)
  fetch_active_markets() → ~3,000 Gamma markets, each with volume and volume_24h
  select_markets()       → top 500 by (short_dated_bonus, -volume_24h, -liquidity, ...)
  websockets.connect(max_size=10MB) → subscribe to all 500 with stable frames
  seed into app_state.markets with volume_24h populated

MarketSelector._discover_and_seed() (every 15 min, independent)
  fetch_active_markets() → same Gamma data
  filter: (volume_24h OR volume) >= $10k   ← FIXED
  seed candidates not already in state (top Falcon spiking extras)

MarketSelector.filter_and_rank() (every 15 min)
  evaluates all markets in state (~600)
  spread >= 2¢, volume_24h >= $10k, bid 5–95¢, resolves 4h–7d
  ranks by spread × volume_24h × fee_mult × depth_mult × falcon_mult
  → up to 30 selected → currently 17 qualify, all 17 reach QuoteEngine
```

All 17 qualifying markets are naturally in CLOBMonitor's top 500 by `volume_24h`. They receive real-time WS price feeds identical to the current 250. No stale-price risk.

---

## What Does NOT Change

- Circuit breakers (inventory cap, adverse selection, rapid double-fill, daily loss)
- Per-market inventory cap (50 shares), total cap (200 shares), cooldown durations
- QuoteEngine spread calculation, fair-value computation, ladder logic
- OrderManager, FillPoller, ShadowFillPoller, MarkoutTracker
- MarketSelector filter thresholds (`_MIN_DAILY_VOLUME`, `_MIN_SPREAD`, `_MIN_BID`, `_MAX_BID`, `_MAX_DAYS_TO_RESOLVE`, `_MIN_DAYS_TO_RESOLVE`)
- `_MAX_ACTIVE_MARKETS=30` — maker still quotes at most 30 markets simultaneously
- Any existing test

---

## Tests

Two new tests in `tests/market/test_clob_selection.py`:

### `test_select_markets_respects_500_cap`

Build a `token_map` with 600 fake markets. Call `select_markets()`. Assert `len(result) <= 500`. Ensures the raised cap is enforced, not bypassed.

### `test_discover_seed_uses_volume_24h`

Mock `fetch_active_markets` to return one market with `volume=0` (zero all-time CLOB volume) and `volume_24h=50_000` (active today). Call `_discover_and_seed()`. Assert the market appears in `app_state.markets`.

This test **fails red** against the current code (volume=0 < $10k threshold) and goes **green** after the fix — directly proving the bug and validating the change.

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| WS reconnect frequency increases at 500 markets | Low | Existing reconnect-in-10s logic handles it; tested at 500–700 with stable data flow |
| More markets in state slows `filter_and_rank` | Negligible | 600 vs 590 ContractState dict iteration; measured impact is microseconds |
| New markets generate adverse-selection fills | Normal | Circuit breakers unchanged; adverse selection cooldown fires at 5/5 same-side fills same as today |
| Markets selected today may not qualify tomorrow | Expected | MarketSelector refreshes every 15 min; markets falling below thresholds exit naturally |
