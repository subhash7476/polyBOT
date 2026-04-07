# POLYMARKET BOT v3.0 — Autonomous Edge Hunter

## MISSION

The bot is built. 401 tests pass. 10 signals. 23 modules. Paper mode works.

Your job now is to **RUN it, WATCH it, LEARN from it, and MAKE IT PROFITABLE.**

You are the quant researcher AND the operator. Work autonomously in a continuous loop:
run → collect data → analyze → adjust → run again. Do NOT ask me what to do next.
Only stop for: missing API keys, broken feeds, or a risk limit question.

---

## CURRENT BOT STATE

```
D:\bot\polymarket-bot\
├── main.py                      # Entry point
├── config.py                    # All weights, thresholds, constants
├── fills.jsonl                  # Trade log (signals + outcomes)
├── market/clob_monitor.py       # Gamma API → CLOB WebSocket
├── market/market_screener.py    # Ranked watchlist by edge opportunity
├── engine/                      # Bayesian engine + all signals
│   ├── probability.py           # 7 feed signals + GBM prior
│   ├── flatline.py              # 48h stagnation (weight 0.20)
│   ├── orderbook_imbalance.py   # Bid/ask depth ratio (weight 0.10)
│   ├── volume_divergence.py     # Volume spike no price move (weight 0.10)
│   ├── arb_scanner.py           # Monotonicity + cross-temporal
│   └── bayesian.py              # log_odds += weight × strength × confidence
├── trading/                     # EV gate → Kelly → risk → executor
├── calibration/                 # Brier, attribution, weight optimizer
├── monitoring/alerts.py         # Telegram alerts
└── dashboard/                   # Live terminal dashboard
```

**Signals (10 total):**
| Signal | Weight | Category |
|--------|--------|----------|
| Lognormal GBM prior | — | crypto |
| vol_skew | 0.15 | crypto |
| funding_rate | 0.15 | crypto |
| onchain_netflow | 0.10 | crypto |
| macro_dxy | 0.10 | crypto |
| stablecoin_supply | 0.05 | crypto |
| btc_hashrate | 0.05 | crypto (BTC only) |
| flatline | 0.20 | all categories |
| orderbook_imbalance | 0.10 | all categories |
| volume_divergence | 0.10 | all categories |

**Market categories:** crypto, rates (count + meeting), macro, election, event
**Risk limits:** 5% fractional Kelly, $50 cap/trade, 20% group exposure, daily loss limit
**EV gate:** 3% minimum after 2% fees + 0.5% adverse selection

---

## PHASE 1: BOOT & VALIDATE (Do this immediately)

### Step 1: Health check
```bash
cd D:\bot\polymarket-bot
pytest                          # All 401 tests must pass
python -m market.market_screener  # Verify Gamma API connectivity
```

If tests fail, fix them before proceeding. If feeds are down, diagnose and fix.

### Step 2: Launch paper mode
```bash
python main.py   # PAPER=true is default
```

Let it run. Monitor the first 30 minutes of output. Check:
- [ ] All feeds connecting (Deribit, Binance, DeFiLlama, Blockchain.com, FRED, NY Fed, Yahoo)
- [ ] CLOB WebSocket streaming prices
- [ ] Markets being discovered and parsed
- [ ] Signals firing (check logs for `log_odds` adjustments)
- [ ] EV gate evaluating trades
- [ ] `fills.jsonl` receiving entries

If any feed fails silently, the bot trades blind on that signal. Find and fix.

### Step 3: Run the screener
```bash
python -m market.market_screener
```

Study the output. Which markets rank highest for edge opportunity?
Are there market types we're parsing but have no signal coverage for?
Log observations in `discoveries.md`.

---

## PHASE 2: THE DATA COLLECTION GRIND

**Leave the bot running in paper mode for a minimum of 48 hours.**

During this time, your job is to monitor and analyze, not change code.

### Every 6 hours, run this analysis cycle:

```bash
# 1. How many signals have we logged?
python -c "import json; lines=open('fills.jsonl').readlines(); print(f'{len(lines)} signals logged')"

# 2. Calibration snapshot
python -m calibration.metrics

# 3. Detailed attribution (once you have 20+ fills)
python -m calibration.metrics --detailed

# 4. Check for arb opportunities found
grep -c "arb" fills.jsonl

# 5. Market screener — what's the opportunity landscape?
python -m market.market_screener
```

### What to look for in early data:

**Signal health:**
- Are all 10 signals actually firing? Or are some always silent?
- If flatline never fires: are there markets within 72h of expiry with low price movement? If not, it's a coverage gap, not a bug
- If orderbook_imbalance never fires: check if `clob_monitor.py` is actually fetching depth data
- If volume_divergence never fires: threshold might be too aggressive (2× might be too high)

**Market coverage:**
- How many of the 3,000 discoverable markets does `contract_parser.py` successfully parse?
- What percentage get skipped? Why? (unparseable question format? unsupported category?)
- Are there high-volume markets we're skipping that we could support with minor parser changes?

**EV distribution:**
- What's the distribution of calculated EVs? Are most trades barely above the 3% threshold or well above?
- If everything clusters at 3-4% EV, our edge is thin and fees will eat it
- If we see 8-15% EV opportunities, those are the sweet spots — what signals drive them?

**Log everything in `discoveries.md` with timestamps and sample sizes.**

---

## PHASE 3: EDGE ANALYSIS (After 48h+ of paper data)

Once `fills.jsonl` has 50+ entries with some resolved outcomes, the real work begins.

### 3A: Per-Signal Attribution
```bash
python -m calibration.metrics --detailed
```

This tells you which signals are actually predictive vs adding noise.

**Decision matrix:**
| Signal Outcome | Action |
|----------------|--------|
| Signal has positive edge AND statistical significance (p < 0.10) | Keep, possibly increase weight |
| Signal has positive edge but low sample size (< 20 trades) | Keep collecting, don't change weight yet |
| Signal has zero or negative edge with 30+ samples | Reduce weight to 0.05 or disable |
| Signal never fires | Debug why — is it a data issue or a threshold issue? |

### 3B: Weight Optimization
```bash
python -m calibration.weight_optimizer
```

**Rules for weight changes:**
1. NEVER auto-apply. The optimizer outputs recommendations — you review them
2. Compare optimized weights to current weights in `config.py`
3. If a signal's optimized weight is <0.03, it's probably noise — consider disabling
4. If a signal's optimized weight is >2× its current weight, it's underweighted — bump it
5. Make ONE weight change at a time, then collect 50 more fills before the next change
6. Log every weight change in `discoveries.md` with before/after and reasoning

### 3C: Category-Level Edge Analysis

Break down performance by market category:

| Category | Questions to Answer |
|----------|-------------------|
| crypto | Do vol_skew and funding_rate actually predict direction? Or does GBM prior do all the work? |
| rates | Is the Poisson model well-calibrated? Is lambda tracking reality? |
| macro | Do we have enough FRED data for meaningful CPI/GDP signals? |
| election | Flatline-only — is that enough signal, or are we trading blind? |
| event | Same as election — flatline + time-decay, is it working? |

**If a category has negative edge after 30+ trades: stop trading it.**
Comment out the category in `contract_parser.py` and focus capital on what works.

### 3D: The Flatline Deep Dive

This signal has the highest weight (0.20) based on the anecdotal 79-81% claim.
Your job is to validate or kill it with real data.

Questions to answer:
1. How many markets have entered flatline state since we started monitoring?
2. Of those that resolved, what was the actual win rate?
3. Does the 2-cent threshold (max price range < $0.02 over 48h) work, or should it be tighter/looser?
4. Is there a minimum leading-side price where flatline works? (e.g., >0.70 vs >0.60)
5. Does flatline edge differ by category? (crypto flatlines vs election flatlines)

**If flatline win rate < 60% after 30+ resolved trades:** reduce weight to 0.10.
**If flatline win rate < 55% after 50+ resolved trades:** disable it entirely.
**If flatline win rate > 70%:** increase weight to 0.25 and widen the scanner to 96h pre-resolution.

Log all findings in `discoveries.md`.

---

## PHASE 4: AUTONOMOUS PATTERN DISCOVERY

Don't just validate MY hypotheses. Find your own.

### 4A: Data Mining on fills.jsonl

Write analysis scripts (save in `analysis/` folder) that answer:

1. **Time-of-day patterns:** Do signals fired at certain hours have higher win rates?
   - Polymarket activity likely peaks during US market hours
   - Are overnight signals (low liquidity) more or less accurate?

2. **Day-of-week patterns:** Weekend markets behave differently (lower volume, wider spreads)
   - Do weekend signals have higher adverse selection?

3. **Expiry proximity:** How does signal accuracy change as markets approach resolution?
   - Hypothesis: signals are more accurate 24-48h before resolution than 7+ days out
   - If true: increase position size for near-expiry signals

4. **Spread as a predictor:** Do wide-spread markets (>$0.10) have more edge than tight ones?
   - Wide spread = less efficient = more opportunity, but also more slippage

5. **Volume thresholds:** Is there a minimum market volume below which our signals don't work?
   - Low volume = easier to move price = our signals might BE the price movement

6. **Signal combinations:** Which pairs of signals, when they agree, have the highest win rate?
   - e.g., flatline + orderbook_imbalance agreeing → is that stronger than either alone?
   - Build a correlation matrix of signal co-occurrence and joint win rates

7. **Arb frequency:** How often does the arb scanner find real opportunities?
   - Are monotonicity violations actually tradeable after fees?
   - Cross-temporal arbs — do they persist long enough to execute?

### 4B: New Signal Ideas to Test

If data mining reveals a pattern, build it as a new signal module:

1. **Expiry countdown acceleration:** As resolution approaches, does price converge to 0 or 1 faster than a random walk? If yes, riding the convergence is free money
2. **Whale wallet tracking:** Large CLOB orders (>$5k) appearing — does following them within 5 minutes have edge?
3. **Category momentum:** If crypto markets are all moving YES, does that predict the next crypto market will also go YES?
4. **Resolution clustering:** Do markets that resolve on the same day influence each other's prices?
5. **Feed correlation strength:** When Deribit DVOL and Binance funding rate disagree, which one wins?

For each new pattern:
- Write the analysis script in `analysis/`
- Require minimum 30 data points before declaring a pattern
- Calculate p-value (is this better than random?)
- If valid: implement as new signal module in `engine/`, add tests, integrate into Bayesian engine
- If invalid: document why in `discoveries.md` and move on

---

## PHASE 5: GO-LIVE CRITERIA

**The Paper Validation Checklist must pass BEFORE touching PAPER=false:**

```
[ ] 50+ signals logged in fills.jsonl
[ ] 20+ resolved outcomes recorded
[ ] Brier score < 0.20
[ ] Mean edge > 3%
[ ] No single day exceeds 5% bankroll loss
[ ] At least ONE signal has positive attribution with p < 0.10
[ ] Calibration curve reviewed and documented
[ ] Weight optimizer has run and recommendations reviewed
[ ] discoveries.md has 5+ documented findings
```

### Go-Live Procedure (when checklist passes):

1. Set `BANKROLL_USDC` to 10% of intended capital (start small)
2. Set `MAX_TRADE_SIZE_USDC` to $10 (not $50 yet)
3. Set `PAPER=false`
4. Run for 3 days with these reduced limits
5. Monitor every trade via Telegram alerts
6. If profitable after 3 days at small size:
   - Increase `MAX_TRADE_SIZE_USDC` to $25
   - Run another 3 days
7. If still profitable:
   - Increase to full `BANKROLL_USDC` and $50 cap
8. If any 3-day window is net negative:
   - Back to paper mode
   - Run `python -m calibration.metrics --detailed`
   - Find what changed
   - Fix before going live again

**NEVER skip straight to full size. Always graduate through the steps.**

---

## PHASE 6: CONTINUOUS OPERATION LOOP

Once live, this is your daily cycle. Run it forever:

```
MORNING (or every 8 hours):
├── Check fills.jsonl for overnight trades
├── Run: python -m calibration.metrics
├── Check: any risk limits hit?
├── Check: all feeds still connected?
├── Run: python -m market.market_screener → new opportunities?
└── Log status in discoveries.md

WEEKLY:
├── Run: python -m calibration.metrics --detailed
├── Run: python -m calibration.weight_optimizer
├── Review per-signal attribution → any signal gone stale?
├── Review category breakdown → any category to disable?
├── Check edge decay → rolling 30-day edge still > 2%?
└── Make ONE adjustment if data supports it (weight change, threshold change, or new signal)

MONTHLY:
├── Full backtest of all strategies on accumulated data
├── Compare live performance to backtest expectations
├── Are new market types worth supporting? (sports? entertainment?)
├── Is the Polymarket ecosystem changing? (new fee structure? API changes?)
└── Write a monthly report in discoveries.md
```

### Edge Decay Protocol

Edges get arbitraged away. When you detect decay:

1. **Rolling 30-day edge drops below 2%:** Alert. Investigate which signals degraded.
2. **Rolling 30-day edge drops below 1%:** Reduce position sizes by 50%.
3. **Rolling 30-day edge goes negative:** Back to paper mode immediately.
4. **A signal that was positive goes negative for 3 consecutive weeks:** Disable it.

The response to edge decay is ALWAYS: collect more data, analyze, adapt.
Never just increase bet size to compensate for a shrinking edge.

---

## OPERATING RULES

1. **No code changes while live without tests.** Every change: write test → run pytest → verify → deploy.
2. **One variable at a time.** Never change two weights simultaneously. You can't attribute results.
3. **discoveries.md is your lab notebook.** Date every entry. Include sample sizes. Note confidence levels.
4. **When in doubt, don't trade.** Paper mode is always available. Capital preservation > returns.
5. **Respect the Kelly.** If Kelly says bet $3, bet $3. Don't round up to $10 because you "feel confident."
6. **Telegram alerts are your eyes.** Set them up. Check them. If you stop getting alerts, something broke.
7. **The bot should be BORING.** Profitable trading is boring. If it's exciting, you're taking too much risk.

---

## IMMEDIATE FIRST ACTIONS

Do these right now, in this order:

```
1. cd D:\bot\polymarket-bot
2. pytest                              # Verify everything passes
3. python -m market.market_screener    # See what markets exist right now
4. python main.py                      # Start paper mode
5. Wait 1 hour
6. python -m calibration.metrics       # First data checkpoint
7. Study fills.jsonl — what signals are firing?
8. Begin Phase 2 analysis cycle
9. Document everything in discoveries.md
```

**The edge is not in the code. The code is done. The edge is in the DATA.**
**Go collect it. Analyze it. Trade on what you find. Start now.**
