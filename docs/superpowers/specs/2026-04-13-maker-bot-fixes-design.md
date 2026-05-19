# Maker Bot Fixes — Design Spec
**Date:** 2026-04-13  
**Scope:** Three targeted fixes to `clob_monitor.py` and `market_selector.py`

---

## Background

Diagnostic after overnight power-outage recovery revealed three bugs:

1. Markout tracker reads frozen mid-prices — all 22 markout records showed `0.0` at T+5/30/60s despite real trades crossing at significantly different prices.
2. Election markets (Iowa/Wisconsin/Minnesota governors, Nov 2026) bypass the `far_future` date filter because their `end_date_iso` field is empty.
3. High-volume Falcon spiking markets (Iran, Israel, Hungary) never reach `AppState` because the seed ranking scores them low (tight spread kills the `spread × vol` score), so the DIAG log can only show `NOT_IN_STATE` with no further detail.

---

## Fix 1 — `_handle_trade` mid update

**File:** `market/clob_monitor.py`  
**Function:** `_handle_trade()`

### Root cause
`_handle_trade()` routes `last_trade_price` WebSocket events to `trades_q` for shadow fill simulation but **never updates `cs.best_bid` / `cs.best_ask`** in `ContractState`. For thin weather markets that trade in short bursts, no `price_change` events arrive between trades and the T+5/30/60s markout sampling windows. The mid stays frozen at its last order-book snapshot value throughout.

### Fix
After parsing the trade price, apply a directional bound update to `ContractState` — **lockless, matching the existing `_handle_book()` pattern**:

```python
cs = self._state.markets.get(token_id)
if cs is not None:
    if price > cs.best_ask:
        cs.best_ask = price   # taker cleared the ask — ask moved up at minimum
    elif price < cs.best_bid:
        cs.best_bid = price   # taker hit the bid — bid moved down at most
```

The update happens **before** `trades_q.put_nowait()` so the shadow fill poller reads the already-updated mid when it dequeues the event.

### What this changes
- `mid_at_fill` in shadow fills will reflect the actual trade price as a lower bound on the ask (or upper bound on bid), rather than a stale CLOB snapshot.
- Markout T+5/30/60 samples will capture real price movement after fills.
- `_handle_trade` stays synchronous — no signature change, no lock acquisition.

### What this does NOT change
- Shadow fill trigger logic (unchanged).
- The `price_change` / `book` handlers continue to be the authoritative source; the trade-price update is a conservative bound, not an override.

---

## Fix 2 — Election market date-filter bypass

**File:** `maker/market_selector.py`  
**Functions:** `filter_and_rank()`, `_discover_and_seed()`

### Root cause
The date guard in `filter_and_rank()`:
```python
if cs.end_date_iso:        # skipped when field is empty string
    if days_left > _MAX_DAYS_TO_RESOLVE:
        n_far_future += 1; continue
    if days_left < _MIN_DAYS_TO_RESOLVE:
        n_too_soon += 1; continue
```
Election markets ("Will Republicans win Iowa governor's race?") have no `endDateIso` from the Gamma API (no fixed certification date) and `parse_contract()` cannot extract one from the question text. `end_date_iso` stays `""` → the entire date block is skipped → these 7-month markets are silently treated as eligible.

### Fix
Add a new constant and a pre-check before the existing date block:

```python
_REQUIRE_END_DATE = os.getenv("MAKER_REQUIRE_END_DATE", "true").lower() == "true"
```

In `filter_and_rank()`, before the existing `if cs.end_date_iso:` block:
```python
if not cs.end_date_iso:
    if _REQUIRE_END_DATE:
        n_no_date += 1
        continue
```

Add `no_date={n_no_date}` to the filter log line so the count is visible.

Apply the same guard in `_discover_and_seed()` so date-unknown markets don't enter state unnecessarily.

### What this changes
- Markets with unknown resolve dates are excluded from quoting by default.
- Filter log gains `no_date=N` count for observability.
- `MAKER_REQUIRE_END_DATE=false` env var opts back in to old behavior if needed.

---

## Fix 3 — Falcon top markets not reaching state

**File:** `maker/market_selector.py`  
**Function:** `_discover_and_seed()`

### Root cause
`_discover_and_seed()` ranks Gamma candidates by `spread × vol_rank`. High-volume Falcon markets (Iran $101M/d, Israel $44M/d, Hungary $10M/d) have tight spreads (~0.001-0.002) → near-zero score → eliminated before the top-250 seed window. They never enter `AppState`. The DIAG loop can only report `NOT_IN_STATE` — no way to see whether they'd be `TIGHT` or otherwise.

### Fix
After building the regular candidate list in `_discover_and_seed()`, append Falcon spiking markets that are indexed in the Gamma API data but not already in the candidate list (matched by condition_id). Cap the Falcon force-seed batch at the top 20 by 24h volume.

These markets enter state and flow through `filter_and_rank()` normally — most will be excluded by `tight_spread`. The DIAG log will now show `TIGHT(spread=0.001)` instead of `NOT_IN_STATE`, which gives correct diagnostic information.

### What this changes
- Top Falcon markets always reach `AppState` regardless of spread.
- No change to quoting behavior — `tight_spread` still excludes them from active quoting.
- DIAG log shows accurate exclusion reason for these markets.

---

## Files Changed

| File | Changes |
|------|---------|
| `market/clob_monitor.py` | `_handle_trade()`: add directional bound update to `cs.best_bid`/`cs.best_ask` before `trades_q.put_nowait()` |
| `maker/market_selector.py` | Add `_REQUIRE_END_DATE` constant; add `n_no_date` counter and pre-check in `filter_and_rank()` and `_discover_and_seed()`; add Falcon force-seed batch in `_discover_and_seed()` |

No new files. No new dependencies.

---

## Testing

- **Fix 1:** Unit test in `tests/maker/test_shadow_fill_poller.py` (or new `tests/market/test_clob_monitor.py`) — mock a `last_trade_price` event above `cs.best_ask` and assert `cs.best_ask` is updated; assert mid reflects trade price.
- **Fix 2:** Unit test in `tests/maker/test_market_selector.py` — market with `end_date_iso=""` must be excluded when `_REQUIRE_END_DATE=True`; must pass when `False`.
- **Fix 3:** Unit test — Falcon spiking market with condition_id present in Gamma data but tight spread still reaches the candidate list from `_discover_and_seed()`.

---

## Out of Scope
- Adverse selection root cause (all-SELL pattern) — circuit breaker handles it; deeper analysis needs live fill data.
- Dashboard UI changes — markout data will naturally improve once Fix 1 is deployed.
- Checkpoint save frequency — data loss from power outage was minor; not a blocker.
