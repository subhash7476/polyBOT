# POLYMARKET BOT v3.0 — Autonomous Edge-Finding Mission

## SITUATION

The bot is built. 401 tests passing. 10 signals in the Bayesian engine. Arb scanner, market screener, weight optimizer, calibration suite, Telegram alerts, terminal dashboard — all operational.

**Now it needs to run, collect data, find what actually works, and sharpen itself.**

You are the operator. Run the bot in paper mode, monitor its output, analyze results, tune parameters, and iterate — all autonomously. Only pause for: missing API keys, wallet issues, or decisions that risk real money.

---

## PHASE 1: HEALTH CHECK & LAUNCH (Do this first, ~15 min)

### 1A. Verify environment
```bash
cd D:\bot\polymarket-bot
# Check .env is populated
python -c "from config import *; print(f'Bankroll: {BANKROLL_USDC}, Paper: {PAPER}')"
# Confirm paper mode is ON
# Run full test suite
pytest --tb=short -q
```
If tests fail, fix them before proceeding. Do not skip broken tests.
### 1B. Verify all feeds connect
```bash
# Start bot, watch for feed connection logs
python main.py
# Expected: Deribit WS connected, Binance polling, FRED fetched, Gamma discovery complete
# If a feed fails: check API keys, check network, check if source is down
# Log which feeds are live vs degraded
```

### 1C. Check market coverage
```bash
python -m market.market_screener
# How many markets discovered? How many parseable? How many pass slippage filter?
# Record baseline: X total markets, Y tradeable, Z with active signals
```

### 1D. Review existing fills.jsonl
```bash
python -m calibration.metrics --detailed
# If fills.jsonl exists: what's current Brier score? Mean edge? Per-signal attribution?
# If fills.jsonl is empty/missing: that's fine, we're starting fresh
# Record baseline in discoveries.md
```

---

## PHASE 2: DATA COLLECTION RUN (Let it run 24-72 hours)

### 2A. Start paper trading
```bash
PAPER=true python main.py
```
Leave it running. The bot will:
- Scan markets via Gamma API every cycle
- Collect prices, order book depth, volume data
- Fire signals through the Bayesian engine
- Log every signal + trade decision to fills.jsonl
- Run arb scanner every 30 seconds
- Send Telegram alerts (if configured)
### 2B. While it runs, do parallel analysis

Don't just wait. Use the data that's accumulating:

**Every 6 hours**, run:
```bash
python -m calibration.metrics --detailed
```
Check:
- How many signals have fired?
- Which signals fire most often? (frequency)
- Which signals agree with each other most? (correlation)
- Are any signals NEVER firing? (dead signals — investigate why)
- What's the distribution of EV estimates? (too many marginal? too few?)

**Every 12 hours**, run:
```bash
python -m market.market_screener
```
Check:
- Are we missing high-volume markets? (parser can't handle them?)
- Which categories dominate our watchlist?
- Are there market types we could trade but aren't?

**Log findings in `discoveries.md`** with timestamps.

---

## PHASE 3: FIRST ANALYSIS CYCLE (After 24h+ of data)

### 3A. Signal quality audit
Open `fills.jsonl` and analyze:

```python
import json, pandas as pd
fills = [json.loads(l) for l in open('fills.jsonl')]
df = pd.DataFrame(fills)
# For each signal, compute:
# 1. How often does it fire? (frequency)
# 2. When it fires, does the market move in the predicted direction? (directional accuracy)
# 3. What's the average strength when it fires? (signal conviction)
# 4. Does it fire alone or always with other signals? (independence)
# 5. For resolved markets: did the signal predict correctly? (outcome accuracy)
```

**What you're looking for:**
- Signals that fire frequently AND predict correctly = your edge
- Signals that fire frequently but predict poorly = noise, reduce weight
- Signals that rarely fire but are always right = valuable, maybe increase weight
- Signals that always fire together = redundant, consider merging or dropping one

### 3B. Parameter sensitivity analysis

Test whether current thresholds are optimal:

```
Flatline threshold: currently 2c over 48h
  → Test 1c, 2c, 3c, 5c — which gives best win rate with sufficient sample?

OBI ratio: currently >2.5 / <0.4
  → Test 2.0/0.5, 3.0/0.33, 4.0/0.25 — which balances signal quality vs frequency?

Volume divergence: currently 2× avg + <2% price change
  → Test 1.5×/3%, 2×/2%, 3×/1% — which combination has best predictive power?

EV threshold: currently 3%
  → Are we leaving money on the table at 3%? Would 2% capture more profitable trades?
  → Or are we taking bad trades? Would 4% filter out losers?

Signal filter: currently ≥2 signals + ≥60% agreement
  → Test ≥3 signals, ≥70% agreement — does tighter filter improve win rate enough
    to offset fewer trades?
```

For each parameter test, record in `discoveries.md`:
- Parameter value tested
- Number of qualifying signals at that threshold- Win rate (if resolved data available)
- Projected EV
- Recommendation: keep / tighten / loosen

### 3C. Weight optimization (if 100+ fills)
```bash
python -m calibration.weight_optimizer
```
- Compare optimized weights vs current static weights in config.py
- If optimizer suggests a signal weight should be 0 or near-0 → that signal isn't helping
- If optimizer suggests dramatically different weights → current weights are miscalibrated
- DO NOT auto-apply. Write recommendations to `discoveries.md`. I'll review before changing.

### 3D. Arb scanner audit
- How many arb opportunities has it found?
- What's the average spread on violations?
- Are they real opportunities or data artifacts (stale prices, illiquid markets)?
- Are cross-temporal arbs more common than monotonicity arbs?

---

## PHASE 4: EDGE SHARPENING (Iterative, repeat every 48-72h)

### 4A. Tune what's working
Based on Phase 3 findings, make targeted changes:

1. **Adjust signal weights** — If flatline is crushing it, bump from 0.20 to 0.25. If OBI is noise, drop from 0.10 to 0.05. Always update `config.py`, run tests.

2. **Adjust thresholds** — Tighten parameters that improve win rate. Loosen parameters where we're missing good trades.

3. **Fix dead code paths** — If a market category never generates trades (e.g., election markets have no data), either improve the parser or stop scanning those markets (save API calls).

4. **Add market-specific overrides** — Maybe crypto markets need different EV thresholds than macro markets. Add category-specific config if the data supports it.
### 4B. Hunt for NEW patterns
With accumulated data, go beyond the existing signals:

1. **Time-of-day effects** — Do signals fire more accurately at certain hours? (e.g., during US market hours vs overnight?)
2. **Day-of-week effects** — Weekend markets behave differently? Less liquidity = bigger mispricings?
3. **Category clustering** — Do crypto markets and macro markets respond to the same signals differently?
4. **Resolution proximity effects** — Do signals get more/less accurate as markets approach expiry?
5. **Spread patterns** — Markets with wider spreads: more edge or more risk?
6. **Volume decay** — Markets where volume is declining: are these overlooked opportunities or traps?
7. **Correlation between flatline + OBI** — When BOTH fire, is win rate dramatically higher than either alone?
8. **Arb as a signal** — Markets that recently had arb violations: do they trend after correction?

For each pattern: compute win rate, sample size, p-value. If p < 0.05 AND sample > 30 → it's a candidate signal.

### 4C. Stress test
```python
# Monte Carlo: simulate 1000 portfolios with random trade entry timing
# Does the edge survive randomization? Or is it timing-dependent?

# Regime analysis: separate fills.jsonl into "volatile" vs "calm" periods
# Does the edge exist in both regimes? Or only in calm markets?

# Fee sensitivity: what if fees increase from 2% to 3%? Does edge survive?
```

---

## PHASE 5: GO-LIVE DECISION

### The paper validation checklist (from README) must pass:
- [ ] 50+ signals logged in fills.jsonl
- [ ] 20+ resolved outcomes recorded
- [ ] Brier score < 0.20
- [ ] Mean edge > 3%
- [ ] No single day exceeds 5% bankroll loss
- [ ] Calibration curve reviewed and acceptable
### Additional go-live criteria (stricter):
- [ ] Weight optimizer has been run; weights are data-informed, not just initial guesses
- [ ] At least ONE signal has demonstrated >60% directional accuracy across 30+ resolved trades
- [ ] Per-signal attribution shows no single signal is responsible for >50% of edge (diversified)
- [ ] Arb scanner false positive rate < 20% (arbs are real, not stale data)
- [ ] Bot has run continuously for 72+ hours without crashes, feed disconnections >5 min, or data gaps
- [ ] Discoveries.md has 5+ documented patterns with statistics

### When checklist passes:
1. Write a summary in `discoveries.md`: "Go-live readiness report"
2. Include: expected daily trade count, expected win rate, expected daily P&L range, top 3 risks
3. **STOP AND TELL ME.** Do not flip PAPER=false yourself.

---

## PHASE 6: LIVE OPERATIONS (After I approve)

### First week live:
- Start with 10% of bankroll (set BANKROLL_USDC to 10% of total)
- Monitor every trade via Telegram alerts
- Run calibration daily: `python -m calibration.metrics --detailed`
- Compare live results vs paper results — are they consistent?
- If live win rate is >5% worse than paper → stop, investigate
- Scale to full bankroll only after 7 days of consistent live performance

### Ongoing optimization (weekly cycle):
1. Run weight optimizer
2. Run parameter sensitivity analysis
3. Check for edge decay (rolling 30-day metrics trending down?)
4. Check for new market types to cover
5. Update discoveries.md
6. Restart bot with any config changes
---

## OPERATING RULES

1. **Paper mode stays ON until I explicitly approve live.** No exceptions.
2. **Every code change must pass `pytest`.** Run the full suite. Don't skip.
3. **Log everything to discoveries.md.** Timestamps, sample sizes, confidence levels. If it's not logged, it didn't happen.
4. **Never modify risk.py limits without my approval.** Daily loss limit, position caps, group exposure — these are guardrails, not suggestions.
5. **If something looks too good to be true, it probably is.** 90%+ win rate on a small sample? That's overfitting, not edge. Need 50+ trades minimum.
6. **Prefer many small bets over few large bets.** Kelly sizing already enforces this — don't override it.
7. **When in doubt, sit out.** No trade is better than a bad trade.

---

## WHAT SUCCESS LOOKS LIKE

After 1 week of autonomous operation, I want to see:

```
discoveries.md with:
├── Baseline metrics (day 0)
├── Signal quality audit (day 1)
├── Parameter sensitivity results (day 2)
├── Weight optimization recommendations (day 3+)
├── New pattern candidates (ongoing)
├── Stress test results (day 5+)
└── Go-live readiness report (when ready)

fills.jsonl with:
├── 100+ signal events logged
├── 20+ resolved with outcomes
└── Per-signal readings for every trade

calibration output showing:
├── Brier score trajectory (improving?)
├── Per-signal attribution (which signals earn?)
└── Edge stability (consistent or decaying?)
```

**Start with Phase 1 health check. Then launch paper mode. Then analyze. The data will tell us where the edge is — our job is to listen.**