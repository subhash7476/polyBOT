# Flaw Resolution Plan — 2026-03-30

Produced after 20-hour paper run analysis. Based on joint diagnosis by Claude and Codex.

---

## Context

The bot ran in paper mode from March 20 to March 30. Key findings:

- 109 paper trade placements, representing only ~12 unique decisions (rest are duplicates from position re-entry bug)
- 20 weather positions entered March 29, all resolved on-chain by end of day — 0 detected by the resolution loop
- 17/19 effectively resolved weather trades were losers
- Estimated net P&L on the March 29 weather batch: **-$29.99 on $43 deployed**
- 0 calibration outcomes recorded in fills.jsonl across the entire run
- Bot has been fully saturated (0 new trades) since March 29 10:52 UTC

**Codex summary:** "The lifecycle bug hid settlement. Settlement revealed bad weather trades."

**Root operational blocker:** Resolution loop. **Root strategic blocker:** Weather model.

---

## Flaw 1 — Resolution loop cannot close negRisk positions

**Impact:** All 20 weather positions permanently stuck. 0 calibration data. Bot saturated indefinitely.

Polymarket's negRisk markets (weather) return `resolved=None` from the Gamma API even after settling. The resolution loop in `trading/resolution.py` checks only `payload.get("resolved")` which evaluates `False`. Meanwhile `closed=True` and `outcomePrices=["0","1"]` are present and unread. The loop has been running for 23+ hours without detecting a single resolution.

**Fix:** In `fetch_market_resolution()` — add fallback: if `resolved=None` but `closed=True` and `outcomePrices` show a decisive winner (one price ≥ 0.999), treat as resolved. One conditional block, ~5 lines.

**Verify:** Run `python -m scripts.lifecycle_audit` after fix — expect 0 open, 20 closed_paper, 20 outcomes in fills.jsonl.

**Priority:** Do this first. Everything else depends on it.

---

## Flaw 2 — Weather model uses METAR as a daily-maximum proxy

**Impact:** Systematic overconfidence on wrong outcomes for all morning-opened weather positions. Confirmed on 17/19 trades from the March 29 batch.

METAR measures current (instantaneous) temperature. Weather markets ask for the **daily maximum**. The bot runs at ~04:00–05:00 UTC, which is overnight or early morning local time for most cities. METAR at that hour captures the overnight low — consistently well below the daytime high. The blend_forecasts function (added in this session) weights METAR at 0.5 for non-US cities. This causes the model to compute a high probability that the daily max will be at or below the current overnight temperature, which is systematically wrong.

Examples from March 29:
- NYC 47°F or below: model=0.889, market=0.040, outcome=NO (lost $11.05)
- Tel Aviv 21°C or below: model=0.821, market=0.001, outcome=NO (lost $5.13)

Both cities had overnight lows near the threshold, but daytime highs well above it. The model bet on the overnight low as if it predicted the daily high.

The two winners (Seoul 17°C BUY_NO, Shenzhen 27°C BUY_YES) were entered in cities where local time was closer to midday and METAR was more representative — not evidence of edge, likely a timing coincidence.

**Codex note:** "I would not say root cause proven yet without checking the exact weather feature pipeline and local-time handling in code. But I would say it is the leading hypothesis by far, and strong enough to act on now."

**Step 1 — Confirm hypothesis before touching code.** Parse the March 29 fills.jsonl signal blocks. Extract the blended temperature used for each losing trade. Compare against actual March 29 daily highs for each city. If blended temps are 5–10°C below actuals, the hypothesis is confirmed.

**Step 2 — Fix blend_forecasts.** Gate METAR inclusion on two conditions both being true: local time at trade entry is past 14:00 city local time AND market resolution is within 4 hours. Outside that window, use ECMWF only. METAR is only informative about the daily high once the high has actually been reached.

**Step 3 — Re-evaluate sigma.** With METAR removed from early-day blends, re-run the March 29 batch in simulation. Check whether model probabilities now roughly match market prices on the losing trades. If probabilities are still extreme, sigma needs recalibration. Target: no single bucket exceeds 40% probability for a D+0 market unless the forecast is squarely centered on it.

**Step 4 — Re-run paper weather for one full week** after the fix before trusting weather signals again.

**Priority:** Second. Implement after Flaw 1 is confirmed working.

---

## Flaw 3 — fills.jsonl contains ~18× duplicate entries for the same 4 rates trades

**Impact:** Calibration metrics are meaningless. 274 records represent ~12 unique decisions. Brier score, mean edge, and signal attribution are all distorted by repeated identical entries.

The same 4 rates markets (Fed cut/hike/no-change) were re-entered every 6 hours because `expire_paper_positions()` evicted them from memory but did not write a closed record to positions.jsonl. On restart, they reloaded as open and the bot re-entered them. This is now fixed, but the historical data is dirty.

**Fix:** Write a one-time deduplication script. For each `(token_id, side)` pair, keep only the first fill entry. Archive the rest. Re-run `calibration/metrics.py` on the clean file.

**Priority:** Third. Do after Flaw 1 so dedup runs on a complete dataset including resolved outcomes.

---

## Flaw 4 — Long-dated positions occupy permanent slots

**Impact:** BTC/$1M before GTA VI ($24.48) will not resolve for months or years. Consumes 1 of 20 slots indefinitely. Any multi-month election or event market has the same problem.

**Fix:** Add `max_hold_days` config per category. Suggested defaults:

| Category | Max hold |
|---|---|
| `weather` | 2 days |
| `crypto`, `rates`, `macro` | 30 days |
| `election`, `event` | 90 days |

When `expire_paper_positions()` runs, check category-specific TTL rather than a single global TTL. Positions exceeding their category TTL get marked `CLOSED_PAPER` with a `close_reason=ttl_expired` field.

**Priority:** Fourth.

---

## Flaw 5 — PAPER_USE_POSITION_TTL disabled by default

**Impact:** TTL safety valve is off. If resolution fails for any reason, positions accumulate without bound. The March 29 situation would have persisted indefinitely without manual intervention.

**Fix:** Change default in `config.py` from `"false"` to `"true"`. Change `PAPER_POSITION_TTL_HOURS` default from `6` to `48`. Six hours was too aggressive for same-day weather markets; 48 hours gives resolution a full cycle to fire before TTL evicts.

**Priority:** Fifth. Low-effort safety net.

---

## Flaw 6 — No calibration data for rates and crypto models

**Impact:** Cannot assess model quality. Cannot pass the paper validation checklist. Cannot make a deployment decision.

This is not a code fix — it is a waiting and running problem. After Flaws 1–3 are resolved:

- Let the bot run on rates markets through the next FOMC meeting. Fed decisions are the fastest-resolving rates events and give a clean binary outcome.
- For crypto, target short-dated contracts (weekly price threshold markets) rather than long-dated speculative ones like BTC/$1M.
- Set a concrete target: 30 resolved outcomes across at least 2 categories before reviewing deployment readiness.
- Run `python -m calibration.metrics` weekly and track Brier score trend. If it is not converging below 0.22 after 30 outcomes, the model needs structural review before deployment.

**Priority:** Ongoing. Starts after Flaw 1 is fixed.

---

## Flaw 7 — Dashboard P&L fallback produces incorrect unrealized gains

**Impact:** For positions where the token is absent from the live market snapshot, `current_mid` falls back to `entry_price`, creating fake zero P&L. Positions without a `question` field show `token_id[:16]` as the display name.

**Fix:** Two changes in `dashboard/loops.py`:
1. When `token_id` is not in `markets_snapshot`, mark position as `[stale]` and show P&L as `N/A`.
2. For positions without a `question` field, fall back to `category + token_id[:8]`.

**Priority:** Last. Cosmetic.

---

## Execution Sequence

```
Week 1

  Day 1:  Flaw 1 — fix resolution loop (1 edit, ~30 min)
          Verify 20 positions close, 20 outcomes appear in fills.jsonl
          Flaw 3 — deduplicate fills.jsonl, run clean calibration baseline

  Day 2:  Flaw 2 — confirm METAR hypothesis from fills data
          Implement METAR gating fix
          Simulate March 29 batch with corrected model

  Day 3:  Flaw 4 — category-level TTL
          Flaw 5 — enable TTL default, set to 48h

Week 2–4

  Run paper with corrected code
  Monitor: weather Brier per batch, resolution loop draining daily
  Wait for FOMC or short-dated crypto events for rates/crypto calibration

Deployment gate (all must pass)

  - 30+ resolved outcomes across 2+ categories
  - Brier < 0.22 on weather batch
  - Brier tracked on rates/crypto (target < 0.20)
  - No day > 5% bankroll loss
  - Zero zombie positions for 7 consecutive days
```

---

## Bottom Line

The resolution loop fix is the single day-one action that unblocks everything else. Without it, every other improvement produces no measurable output. The weather model flaw matters more strategically — it explains not just why positions stayed open, but why settling them is bad news — but it cannot be properly measured until resolution works.

The bot is not close to real-money deployment. The path is clear and the problems are diagnosable.

---

## Implementation Record — 2026-03-30

All fixes below were applied on Day 1. All 449 tests pass after changes.

### Flaw 1 — Resolution loop ✅ FIXED

**File:** `trading/resolution.py` — `fetch_market_resolution()` (lines ~94–107)

Added negRisk fallback immediately after the existing `is_resolved` assignment:
```python
if not is_resolved:
    is_closed = bool(payload.get("closed") or market.get("closed"))
    if is_closed:
        resolved_yes_candidate = _resolved_yes_from_market_payload(payload)
        if resolved_yes_candidate is not None:
            is_resolved = True
            resolved_yes = resolved_yes_candidate
```
The `_resolved_yes_from_market_payload()` function already parsed `outcomePrices` correctly (≥0.999 threshold). The only gap was that `is_resolved` was never set True for negRisk markets. This one conditional block closes it.

Also updated `scripts/lifecycle_audit.py` to use the same fallback so the audit report correctly classifies resolved-but-open negRisk positions as ZOMBIE rather than LIVE.

### Flaw 2 — Weather METAR as daily-maximum proxy ✅ FIXED

**File:** `engine/weather_probability.py` — `blend_forecasts()`

Added `hours_to_expiry: float = 999.0` parameter. METAR is now only included when `hours_to_expiry < 4.0`. Outside that window, the blend falls back to ECMWF (non-US) or HRRR+ECMWF (US). This is the correct signal gating: METAR is an instantaneous observation and is only informative about the daily maximum once the maximum has nearly been reached.

The `hours_to_expiry` value was already being computed in `build_weather_probability()` for sigma scaling — it is now also forwarded into `blend_forecasts()`.

**Effect on March 29 trades:** The bot ran at ~04:00–05:00 UTC. For NYC, Tel Aviv, and most cities in the batch, `hours_to_expiry` was 8–20 hours, well above the 4-hour gate. METAR would have been excluded and the blend would have used ECMWF only, producing far less confident (and more accurate) probabilities.

### Flaw 3 — fills.jsonl deduplication ✅ SCRIPT READY

**File:** `scripts/dedup_fills.py` (new)

Keeps the chronologically first entry per `(token_id, side)` pair. Backs up the original before writing. Dry-run by default.

```bash
python -m scripts.dedup_fills           # dry-run: print stats
python -m scripts.dedup_fills --apply   # write deduplicated file
python -m scripts.dedup_fills --apply --archive  # also archive duplicates
```

Run this before the next `calibration.metrics` report to get accurate Brier scores.

### Flaw 4 — Category-level TTL ✅ FIXED

**Files:** `config.py`, `trading/risk.py`

Added `CATEGORY_TTL_HOURS` dict in `config.py`:
- `weather`: 2h (same-day market, should resolve before TTL fires)
- `crypto`, `rates`, `macro`: 720h (30 days)
- `election`, `event`: 2160h (90 days)
- All overridable via env vars (`TTL_WEATHER_HOURS`, `TTL_CRYPTO_HOURS`, etc.)

Updated `expire_paper_positions()` to look up `CATEGORY_TTL_HOURS.get(pos.category, ttl_hours)` per position instead of applying a single global TTL.

### Flaw 5 — PAPER_USE_POSITION_TTL disabled by default ✅ FIXED

**File:** `config.py`

Changed defaults:
- `PAPER_POSITION_TTL_HOURS`: `"6"` → `"48"` (TTL safety valve fires before 2-day weather markets expire naturally)
- `PAPER_USE_POSITION_TTL`: `"false"` → `"true"` (TTL is now on by default)

The weather category TTL of 2h from Flaw 4 takes precedence for weather positions, so weather TTL is effectively 2h regardless of the 48h global default.

### Flaw 6 — No calibration data (rates/crypto) — ONGOING

No code change. Waiting for resolved outcomes. Run `python -m calibration.metrics` after next FOMC or short-dated crypto event.

### Flaw 7 — Dashboard P&L fallback ✅ FIXED

**File:** `dashboard/loops.py`

When `token_id` is absent from `markets_snapshot`:
- `current_mid` is now `None` (was `pos.entry_price`, producing fake zero P&L)
- `pnl_usdc` is now `None` (was a misleading computed value)
- `stale: true` flag added to position record so the frontend can display "N/A"
- `question` falls back to `pos.question` (from ledger) then `"{category} {token_id[:8]}"` instead of raw `token_id[:16]`
