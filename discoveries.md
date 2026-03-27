# discoveries.md — Pattern & Backtest Log

## Format
Each entry: date | sample_size | win_rate | p_value | notes

## Signal Hypotheses (Pre-Backtest)

### Flatline Detector (2026-03-25)
- Hypothesis: Markets where YES price range < 2c over 48h before resolution resolve to leading side ~79-81%
- Parameters: window=48h, threshold=0.02, expiry_gate=72h, min_leading=0.60
- Weight: 0.20 (initial, uncalibrated)
- Target validation: min 50 markets, win_rate > 60%, p-value < 0.05
- Status: HYPOTHESIS — needs 50+ resolved markets in fills.jsonl to validate

### Order Book Imbalance (2026-03-25)
- Hypothesis: bid/ask depth ratio > 2.5 or < 0.4 sustained over 3 readings (15min apart) predicts direction
- Parameters: ratio_high=2.5, ratio_low=0.4, min_readings=3
- Weight: 0.10 (low — expected noise)
- Expected lag: short-term (hours), not predictive near resolution
- Status: HYPOTHESIS — noisy signal, validate first 50 trades

### Volume-Price Divergence (2026-03-25)
- Hypothesis: Volume spike (>2x rolling avg) without price movement (<2%) = accumulation
- Parameters: volume_multiple=2.0, price_move_max=0.02, lookback=24h
- Direction: inferred from price drift since spike (positive drift = bullish)
- Weight: 0.10 (low confidence — direction ambiguous without trade tape)
- Status: HYPOTHESIS — validate direction inference quality

### Complement Arb (2026-03-25)
- Status: DEFERRED
- Reason: In binary Polymarket markets, NO ask = 1 - YES bid by construction.
  YES ask + NO ask >= 1.0 always from mid prices. True complement arb requires
  independent NO token order book tracking (NO token best_ask from CLOB, not derived).
- Integration point: ContractState needs no_best_ask populated from actual NO token feed.
  Add to clob_monitor._handle_book() when NO token asset_id events arrive.

## Phase 1 Baseline — 2026-03-25

**fills.jsonl cleared** (old data backed up to fills.jsonl.bak.20260325_144803). Fresh run starts now.

### Old data analysis (Mar 19-21, 2477 entries — reference only)

**Arb scanner:**
- 2445 violations from a single pair `arb_46866868_10526756`, spread=0.482 (48.2c!)
- Firing every 30s for ~20 hours — persistent false positive from an illiquid/stale market
- ACTION: Investigate this market. Likely needs a min-volume guard on arb violations.
- Cross-temporal arb: 0 violations found in 3 days

**Trade signals:**
- 32 total BUY_YES signals, all on rate-cut count markets ("Will exactly N cuts happen in 2026?")
- 6 unique markets, model_prob=0.251 (Poisson), market_prob=0.006–0.160
- Single signal each: `fed_cut_prob` (strength=-0.48) — passes filter via decisive prior rule
  (|0.251 − 0.5| × 2 = 0.498 > 0.40 threshold)
- Mean EV: 0.173. Max EV: 0.240. These are rate markets where model says ~25% but market prices at 1–16%.
- Fired only Mar 19 then stopped — paper position TTL (6h) expired them, risk.can_trade() blocked re-entry
- 0 resolved outcomes (3-day window too short for these markets)

**Dead signals:**
- `flatline`, `orderbook_imbalance`, `volume_divergence`: 0 fires in 3 days
  - Flatline needs 48h price history AND within 72h of expiry — reasonable for a fresh run
  - OBI needs bid_depth > 0 from CLOB WebSocket (order book events may not be arriving)
  - VPD needs 24h volume history — should start firing after first day

**Crypto signals:**
- 0 trade signals for crypto markets (BTC/ETH/SOL etc.)
- Either parseable crypto markets didn't have enough edge vs market prices, or signal filter blocked them
- Investigation needed: are crypto markets being discovered? Are they passing parse/slippage?

### Config at launch
- BANKROLL_USDC: 500, PAPER: true, MAX_TRADE_SIZE_USDC: 50
- KELLY_FRACTION: 0.05, MIN_EV_THRESHOLD: 0.02 (2%), PAPER_POSITION_TTL_HOURS: 6
- FLATLINE_THRESHOLD: 0.02, FLATLINE_EXPIRY_GATE_HOURS: 72, FLATLINE_MIN_LEADING_PRICE: 0.60
- OBI_RATIO_HIGH: 2.5, OBI_RATIO_LOW: 0.4, OBI_MIN_READINGS: 3
- VPD_VOLUME_MULTIPLE: 2.0, VPD_PRICE_MOVE_MAX: 0.02, VPD_LOOKBACK_HOURS: 24

## Bug Fix: Kelly BUY_NO — 2026-03-25

**Finding:** `fractional_kelly()` was computing negative kelly for all BUY_NO trades, silently returning 0.
- Root cause: Kelly formula `full_kelly = (p*b - q)/b` uses YES model_prob and YES market_price.
  For BUY_NO (model=0.749, mid=0.948): `full_kelly = (0.749×0.055 − 0.251)/0.055 = −3.82` → clamped to 0.
- Fix: pass `(1-model_prob, 1-mid)` to fractional_kelly when `side == "BUY_NO"`.
- Impact: ALL BUY_NO trades were silently skipped before this fix. Bot was only placing BUY_YES trades.
- Confirmed: BUY_NO token 63586620 now executes at $1.31 (model=0.749, market=0.948, EV=0.193).
- Commit: 72d00fc

## Phase 1 Complete — Bot Running — 2026-03-25

**Status:** Bot restarted with Kelly fix. 4 paper trades open.

**Active positions (first 5 min):**
- BUY_YES 18690049: market=0.5%, model=25.1%, EV=24.1%, size=$1.55 — "Will 0 Fed cuts happen in 2026?"
- BUY_YES 95561221: market=3.3%, model=25.1%, EV=21.2%, size=$1.41 — rate cut market
- BUY_YES 83479140: market=1.3%, model=25.1%, EV=23.3%, size=$1.51 — rate cut market
- BUY_NO 63586620:  market=94.8%, model=74.9%, EV=19.3%, size=$1.31 — rate cut market (inverse)

**Model config:** FRED CPI=2.7%, UNRATE=4.4%, lambda=2.12, cut_prob=0.26
The Poisson model says there's a 26% chance of cuts happening in 2026.
Markets are priced at 0.5-3.3% (extreme discount) and 94.8% (premium) — systematic mispricing vs model.

**Scan baseline:** 250 markets subscribed | 50 parseable | 4 signal ok | 4 liquid | 4 ev+
- 504 total parseable markets exist, only 50 subscribed — investigation needed
- 0 crypto signals, 0 flatline/OBI/VPD — expected for day 1

**Open questions (to resolve as data accumulates):**
1. Are the 4 rate-cut market prices (0.5-3.3%) genuine mispricings or illiquid/stale?
2. Why are 454 parseable markets not subscribed? (PARSEABLE_MARKET_RESERVE=50 limit)
3. When will crypto markets start generating signals? (need DVOL feed to stabilize)
4. Is the arb pair (46866868/10526756) with 48c spread a real opportunity or stale data?

## Funnel Unblock — 2026-03-27 (commit ad222ab)

**Problem diagnosed:** Out of ~3,000 markets discovered, only 4 tokens ever traded (all Fed rate cut markets). Zero resolved outcomes. Zero learning. Funnel was clogged at parse, signal, and filter stages.

**Root causes fixed:**
1. **FIX 1 (already done):** Realized vol fallback in `feeds/microstructure.py` — XRP/BNB/DOGE/ADA/AVAX now get proper lognormal priors instead of 0.5 dead prior
2. **FIX 2:** Short-dated crypto parser — "Will BTC go up in next 5 min?" now parseable; also fixed pre-existing "sol" false positive from word "resolution"
3. **FIX 3:** Microstructure probability path — election/event/unknown now routed to `build_microstructure_probability()` (market mid as prior) instead of dying in the crypto engine with no inputs
4. **FIX 4:** Relaxed signal filter — 2+ agreeing microstructure signals can carry a trade; high-confidence flatline (≥0.70) can act alone
5. **FIX 5:** Periodic market re-discovery every 15 min + `PARSEABLE_MARKET_RESERVE` raised 50→150 + short-dated (<24h: +3, 24-72h: +2) sort priority boost
6. **FIX 6:** WS handlers now record price history for ALL subscribed markets (not just parseable ones) — flatline can accumulate 48h of history before the 72h expiry gate fires
7. **FIX 7:** Generic binary catch-all — "Will X happen?" → `category=event`, parseable=True → routes through microstructure path

**Expected impact:**
- Parseable markets: 50 → 500-1000+
- Markets with active signals: 4 → 50-100+
- Unique token IDs traded per day: 4 → 30+
- First resolved outcome: April 29 → hours (short-dated crypto)

**FUNNEL log added:** Each scan now emits `FUNNEL: N discovered | N parsed | N signal_ok | N liquid | N ev+ | N traded | categories: {...}`

**Open questions (to resolve as data accumulates):**
1. Are the 4 rate-cut market prices (0.5-3.3%) genuine mispricings or illiquid/stale?
2. When will short-dated crypto markets start appearing in FUNNEL log?
3. Is the arb pair (46866868/10526756) with 48c spread a real opportunity or stale data?
4. Will flatline accumulate enough history to fire within 48h of bot restart?

## Backtest Results
<!-- Populated as fills.jsonl accumulates resolved markets -->
