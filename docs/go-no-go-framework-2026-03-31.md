# Go / No-Go Framework — Polymarket Directional Bot
**Date:** 2026-03-31
**Status:** Active — 14-day test begins today

---

## Immediate Action

Disable weather trading. `ENABLE_WEATHER_TRADING=false` in `.env` (already the default). Leave it off for the entire 14 days.

**Why it's dead:** 2 wins / 84 losses on resolved weather trades. The market prices real-time temperature observations. The bot's ECMWF model prices yesterday's forecast. The market is structurally right; the model is structurally wrong. This cannot be patched in two weeks.

---

## Honest Prior

This is a directional taker bot on a prediction market that is increasingly priced by sophisticated participants. Every signal the bot uses — DVOL, funding rate, spot momentum — is public data. Retail bots using public data against market makers who have co-located infrastructure and proprietary order flow have thin edge in the best case and negative edge after fees in the average case.

One specific lane has a defensible reason to exist. The rest do not.

---

## Lane Assessment

| Lane | Verdict | Reason |
|---|---|---|
| Same-day weather buckets | Dead | Market prices real observations. Model prices stale forecasts. Structural loser. |
| 24-72h weather | Not now | Requires calibrated sigma from weeks of resolved data. Cannot validate in 14 days. |
| Rates / macro | No edge | Fed cut markets are priced by SOFR futures traders. Taylor rule is public knowledge. You are the dumb money. |
| Market making | Different system | Requires order management, delta hedging, maker rebate infrastructure. Not an extension — a rebuild. |
| Structural arb (negRisk buckets) | Real edge, already taken | Sub-second arb bots close it before any retail participant fills. |
| **Short-window crypto Up/Down** | **Test it** | 5-min resolution = rapid feedback. Bot has live Binance data contemporaneous with Polymarket price. Narrowest information gap available. |

---

## The Only Lane: Short-Window Crypto (Up/Down)

**Why defensible:** The market for "BTC Up or Down, 7:50–7:55 PM ET" is thin. Retail participants guess. The bot has live Binance spot momentum, funding rate, and DVOL. If those signals have any predictive value over 5 minutes, this is where it shows up.

---

## 14-Day Test Plan

### Phase 1: Days 1–5 — Signal validity check

**Goal:** determine if signals predict direction better than chance.

- Crypto fast-exit only. `PAPER=true`. Weather off.
- Entry price gate: 0.03 ≤ price ≤ 0.97 (enforced in code)
- Minimum 2 signals required, both pointing same direction
- Log every blocked trade with reason

**Gate:** ≥ 30 resolved trades. If fewer than 30 resolve in 5 days, market volume is too thin to test.

### Phase 2: Days 6–10 — Calibration check

**Measure:**
- Brier score on resolved trades
- Win rate by signal strength bucket (weak / medium / strong)
- Whether model_prob correlates with actual win rate

**Gate:** Brier score must show evidence of calibration. If every confidence level wins at the same rate regardless of model_prob, the model is not informed — it is noise.

### Phase 3: Days 11–14 — Edge persistence check

**Measure:**
- Is win rate above 52% sustained, or does it revert after 30+ trades?
- Do consecutive losses cluster (regime the model cannot handle), or are they random?
- Realized PnL net of floor and EV threshold.

---

## Continue Conditions (all must be true)

1. ≥ 50 resolved crypto-fast trades
2. Win rate ≥ 54%
3. Brier score < 0.23 (random = 0.25)
4. Mean edge at entry > 3%
5. No single day worse than −5% of bankroll
6. Win rate on strong-signal trades (strength > 0.5) ≥ 58% — if strong and weak signals win at the same rate, there is no signal, only luck

---

## Kill / Pivot Conditions (any one is sufficient)

1. Win rate < 50% after 50 resolved trades
2. Brier score > 0.24 after 30 resolved
3. Strong signals win at same rate as weak signals (< 3pp difference)
4. Fake edge detected: model_prob does not correlate with outcome despite high win rate
5. Stale-market pattern: majority of wins occur on trades where market_prob moved toward model_prob within 2 minutes of entry (entering after the information is already in the price)
6. < 30 resolved trades by day 10

---

## What Counts as Model Overconfidence

Bin resolved trades by model_prob: [0.55–0.65], [0.65–0.75], [0.75–0.85], [0.85–1.0].
If the 0.85+ bin wins at the same rate as the 0.55–0.65 bin, the model is overconfident.

The `calibration_curve()` function in `calibration/metrics.py` measures this. Run it after every 20 new resolved trades.

Any recurrence of the weather pattern — model > 70% on a market priced at 30% — is a red flag. Review manually before continuing.

---

## What Counts as Fake Edge from Stale Markets

Three markers:
1. Entry price consistently below 0.10 or above 0.90 on the losing side
2. Model/market divergence > 0.40 (weather divergence guard extended to crypto in code)
3. Win on "high confidence" trade happened because market_price moved toward model_prob within 5 minutes of entry — that is not your edge, that is entering before an already-incoming price update

---

## What Not to Work On During 14 Days

- Weather model, calibration, or feeds — any of it
- New signal types
- New market categories (rates, macro, election)
- Live trading infrastructure, redemption flows, Telegram alerts
- Signal weight optimization or EV threshold tuning (no data to tune from yet)

**Only acceptable code work:** crypto-fast data quality bugs, calibration report improvements (win rate by signal strength), price cap on entry.

---

## Final Recommendation

**Narrow and test, with a hard kill date.**

The weather lane is dead until you have 100+ calibrated resolved trades showing the model beats the market at the 24-72h horizon. That is months away, not weeks.

The crypto fast-exit lane is the only path worth testing now. It is genuinely uncertain whether it will work. A small retail bot using public signals against sophisticated makers has structural disadvantages.

**If kill conditions hit:** shelve the directional taker strategy. The viable alternative is market making — quoting both sides on liquid crypto markets, earning the spread rather than paying it. That is a different project requiring a rebuilt execution layer.

**If continue conditions hit:** you have a narrow, fragile edge in short-window crypto. Run it at minimum size. Keep paper mode running in parallel. Recalibrate signal weights every 50 resolved trades. Expect the edge to decay.

---

## Evidence That Triggered This Framework

| Metric | Value |
|---|---|
| Total resolved trades (weather) | 86 |
| Wins | 2 |
| Losses | 84 |
| Win rate | 2.3% |
| Typical market_prob on losses | 0.001–0.010 |
| Typical model_prob on losses | 0.05–0.89 |
| Root cause | Model using stale ECMWF forecasts vs market pricing real-time observations |
| Secondary cause | No entry price floor — bot bought near-resolved tokens at 0.15¢ |
| Seoul 18°C incident | 3 re-entries on same token after resolution, −$32.67, triggered daily loss limit |
