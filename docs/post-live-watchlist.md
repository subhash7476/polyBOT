# Post-Live Watchlist

Items to investigate, tune, or implement once live trading data is available.
Each item lists **what to look for in logs/data** and **what to do** if the signal is confirmed.

---

## 1. Per-Market Fill-Rate Sizing

**Status:** Deferred — paper Poisson model makes per-market fill rates meaningless in paper mode.

**What to implement:** Track `fill_count_by_token` in `MakerState` over a rolling 24h window.
Pass the per-token fill rate into `compute_regime_score` as a new dimension — markets with near-zero
live fill rate should shrink `size_multiplier`; frequently-filled markets can support larger size.

**What to look for in live logs:**
- Markets with many quote reprice cycles but zero or near-zero fills — sizing down is correct.
- Markets that fill faster than expected and hit the inventory cap repeatedly — sizing up is correct.
- Confirm in `maker_data/maker_fills/maker_fills_YYYY-MM-DD.jsonl`: token-level fill counts per session.

**Trigger:** After 500+ live fills across 2+ sessions. Only tune once the fill-rate distribution
across markets is visible.

---

## 2. Rolling P&L Hard-Disable Gate

**Status:** Partially covered — the existing 30-min adverse-selection cooldown and 3%-bankroll daily
loss limit approximate this. A "permanent disable until operator restart" gate is missing.

**What to implement:** In `inventory.py`, after the daily-loss check, add:
```python
if self._maker.session_pnl <= -HARD_STOP_SESSION_LOSS:
    self._maker.hard_stopped = True
    await self._cancel_q.put(CancelAll("*"))
    log.critical("HARD STOP: session P&L threshold breached — quoting disabled until restart")
```
`hard_stopped` checked at the top of `QuoteEngine._reprice` — skip all markets if set.

**What to look for in live logs:**
- Sessions where the 3% daily loss fires multiple times in the same day (repeated cooldown cycles
  without recovery). That means the loss limit is re-arming too aggressively.
- Large single-session swings in `maker_lifetime.json → by_date → cash_pnl` that the existing
  gates did not catch.

**Trigger:** Any live session where cumulative session loss exceeds ~2× the expected spread capture.

---

## 3. Markout Multiplier Calibration (`market_selector.py`)

**Status:** Implemented (commit `1f48b79`) — formula is `clamp(1.0 + avg_30s * 100, 0.2, 2.0)`.

**What to look for in live logs:**
- `maker.market_selector.log` lines containing `markout×` — check which markets are being
  penalised/promoted and whether the direction matches intuition.
- If high-volume markets with good spread capture are being wrongly deprioritised due to
  noisy markout data (small sample size), consider requiring a minimum fill count before
  the multiplier activates (e.g. `n_fills >= 10` before applying `markout_mult != 1.0`).
- If the multiplier is too aggressive (a single adverse fill killing a good market), consider
  widening the clamp or smoothing with an EMA instead of a simple average.

**What to tune:**
- Sensitivity constant (currently 100) — if markets oscillate in/out of selection every cycle,
  reduce to 50.
- Min-fill guard — add `if fill_count < 10: markout_mult = 1.0` to prevent premature penalisation
  of new markets with insufficient data.

---

## 4. Pre-Resolution Flatten Threshold Tuning

**Status:** Implemented at T-2h (commit `2880461`). The 2h window was chosen conservatively.

**What to look for in live logs:**
- `maker.quote_engine.log` lines containing `PRE-RESOLUTION FLATTEN` — check how much inventory
  is being held at T-2h and whether the aggressive_exit orders fill quickly.
- If inventory is repeatedly failing to close within 2h (market is illiquid at expiry), widen
  the window to T-4h.
- If the 2h window is triggering on markets with tiny residual inventory (< 1 share), raising
  `PRE_RES_INV_THRESHOLD` from 0.5 to 1.0 reduces unnecessary order churn.
- Track resolution P&L via the CLOB `/last-trade-price` script (see CLAUDE.md) after each session
  to confirm residual inventory at resolution is trending toward zero.

---

## 5. Adverse Selection Rate by Category

**Status:** The adversity pipeline is fully wired (`_check_adverse_selection`, VPIN, Falcon, regime
score) but the per-category breakdown has never been observed in live data.

**What to look for in live logs:**
- `maker.inventory.log` lines containing `ADVERSE SELECTION` — tally by category (weather, sports,
  politics, crypto). If one category consistently dominates, consider adding it to
  `MAKER_EXCLUDED_CATEGORIES` or raising its spread multiplier floor in `CATEGORY_SPREAD_MULTIPLIER`.
- `fills_markout.jsonl` — break avg_30s by category. A category with persistent negative markout
  is being adversely selected at the category level, not just the market level.

**What to implement if confirmed:**
- Per-category adverse-selection counter in `MakerState`.
- Auto-exclusion logic: if a category's 24h adverse rate > 60%, temporarily exclude it from
  `filter_and_rank` (same mechanism as `_EXCLUDED_CATEGORIES` but dynamic).

---

## 6. Inventory Cap Hit Rate

**Status:** Cap fires are logged (`INVENTORY CAP`) but no aggregate summary is produced per session.

**What to look for in live logs:**
- Cap hits should be near-zero after the pre-resolution flatten guard and markout ranking are
  active. A cap hit rate > 5% of fills means inventory is being built faster than it drains.
- Repeated cap hits on the same token within a session trigger escalating cooldowns (300s → 600s →
  1200s → max 3600s). Verify this is happening by checking `inventory_cap_hits` in the checkpoint.
- If cap hits are zero for extended periods, consider relaxing the cap from 10 shares (weather) to
  allow more position flexibility — but only after markout is consistently positive.

---

## 7. Quote Uptime vs Fill Rate Relationship

**Status:** `quote_uptime` is tracked but has never been calibrated against live fill rates.

**What to look for in dashboard/logs:**
- Dashboard `quote_uptime` should be > 90% for active markets. Drops below 80% indicate
  frequent cooldowns or cancellation storms — investigate the cause in `maker.order_manager.log`.
- Compare `quote_uptime` to `total_fills / session_hours`. In a healthy market, higher uptime
  should correlate with more fills. If uptime is high but fills are zero, the quotes are
  outside the incentive spread or the market has no taker activity.
- Check `maker.market_selector.log` for `incentive_spread miss` warnings — those markets earn
  zero Liquidity Rewards even though the bot is quoting them.

---

## 8. Liquidity Rewards Earnings Verification

**Status:** The bot quotes within `max_incentive_spread` when possible, but rewards have never
been observed in production. The three rewards endpoints are not yet wired to the dashboard.

**What to do on Day 1 of live trading:**
- Authenticate and hit `GET https://clob.polymarket.com/rewards/earnings` to confirm rewards
  are accruing for the previous day.
- Hit `GET https://clob.polymarket.com/rewards/percentages` to see real-time reward % share.
- If rewards are zero despite quoting inside `max_incentive_spread`, check that:
  - `min_incentive_size` is being met (dashboard shows `base_size` per market)
  - The Polymarket account is enrolled in the rewards program

**What to implement after confirming rewards work:**
- Wire the three endpoints to the dashboard so daily rewards are visible alongside realized P&L.

---

## 9. NegRisk Sibling Guard — Threshold Tuning

**Status:** The sibling guard (`negrisk_sibling_guard`) in `quote_engine.py` cancels losing
weather buckets when a sibling hits `mid > NEGRISK_SIBLING_THRESHOLD`. Threshold is hardcoded.

**What to look for in live logs:**
- `maker.quote_engine.log` lines containing `negrisk_sibling_guard` — check whether it fires
  correctly and how long before resolution it typically fires.
- If it fires too late (sibling already at 0.95+ before we cancel), move the threshold from 0.85
  to 0.75.
- If it fires spuriously (sibling temporarily spikes then reverts), add a debounce: require
  `mid > threshold` for two consecutive reprice cycles before cancelling.

---

## 10. Session P&L Drift (checkpoint `realized_pnl` vs true resolution P&L)

**Status:** Known gap documented in CLAUDE.md. `maker_checkpoint.json` zeroes inventory on
resolution but does not book the resolution cash into `realized_pnl`. The checkpoint's
`realized_pnl` reflects only spread-capture round-trips, not resolution payouts.

**What to do each live session:**
- Run the CLOB `/last-trade-price` resolution P&L script (see CLAUDE.md) to get the full
  economic picture including resolution payouts.
- If resolution P&L is systematically negative (losing money at resolution), the pre-resolution
  flatten guard is not closing positions fast enough — widen the window or lower
  `PRE_RES_INV_THRESHOLD`.
- If resolution P&L is consistently zero or positive, the checkpoint gap is tolerable and
  the dashboard P&L understatement is acceptable.

**Longer-term fix:** Book resolution cash into `realized_pnl` in `inventory.py`
`_expire_paper_positions()` so the checkpoint matches the true economic result.

---

## Pre-Live Gate (do not scale until all pass)

| Gate | Metric | Target |
|---|---|---|
| Markout quality | `avg_markout_30s` | ≥ 0 |
| Adverse selection | `adverse_rate_30s` | stable, not rising |
| Net P&L | `total_realized_pnl` | > 0 over 3+ sessions |
| Inventory discipline | Cap hits per session | near zero |
| Quote uptime | `quote_uptime` | > 90% |
| Resolution P&L | CLOB resolution script | ≥ 0 on average |
