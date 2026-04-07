# POLYMARKET BOT v3.0 — Edge Enhancement Mission

## CONTEXT: What Already Exists (DO NOT REBUILD)

You are working on a **production Polymarket bot** that is already operational. Study the codebase before touching anything. Here is the architecture:

```
Feeds (Deribit, Binance, DeFiLlama, Blockchain.com, FRED, NY Fed, Yahoo)
  -> AppState (FeedState + ContractState)
Gamma API -> CLOBMonitor -> AppState
AppState -> trading_loop + arb_scan_loop -> fills.jsonl
```

**What's already built and working (222 tests passing):**
- Multi-factor Bayesian signal engine (logit-additive: `log_odds += weight × strength × confidence`)
- Lognormal GBM prior for crypto (BTC/ETH/SOL/XRP/BNB/DOGE/ADA/AVAX)
- Poisson model for Fed rate cut count markets
- Normal forecast-error model for macro data-release markets (CPI/GDP/NFP)
- Signal filter: ≥2 signals + ≥60% agreement, OR decisive prior (≥40%) + 1 confirming signal
- 7 live feeds: Deribit DVOL + vol skew, Binance funding rates, DeFiLlama stablecoins, Blockchain.com hashrate, FRED macro, NY Fed SOFR, Yahoo DXY
- EV gate: 3% minimum after fees (2%) + adverse selection (0.5%)
- 5% fractional Kelly, $50 hard cap per trade
- Risk manager: daily loss limit, position cap, 20% direction-bucketed group exposure
- Slippage model: rejects volume <$10k or spread >15c
- Arbitrage scanner: monotonicity violation detector for threshold chains
- Paper/live executor via py-clob-client
- Calibration tracker: Brier score, calibration curve, mean edge → fills.jsonl
- Contract parser: asset/direction/target/expiry/cut_count extraction

**What it currently trades:** Crypto price markets, Fed rate cuts, macro data releases.
**What it currently skips:** FDV, token launches, sports, entertainment.

---

## YOUR MISSION: Find New Edges, Bolt Them Into the Existing Engine

You are NOT rebuilding. You are enhancing. Every new feature must integrate with the existing `AppState → engine → ev_gate → kelly → risk → executor` pipeline.

Work autonomously. Execute, don't ask. Only stop for genuine blockers (missing keys, ambiguous risk parameters).

---

## PHASE 1: RECONNAISSANCE — Study Reference Repos

Clone these and extract ideas. Do NOT copy-paste code. Understand patterns, then implement within our architecture.

```bash
git clone https://github.com/caiovicentino/polymarket-mcp-server.git ~/recon/polymarket-mcp-server
git clone https://github.com/FiatFiorino/polymarket-assistant-tool.git ~/recon/polymarket-assistant-tool
git clone https://github.com/Polymarket/agents.git ~/recon/polymarket-agents
```

**What to extract:**

| Repo | Look for | Integrate as |
|------|----------|-------------|
| `polymarket-mcp-server` | Their 45-tool structure, safety guardrails, real-time monitoring patterns, how they handle market categorization | Better monitoring in our `calibration/` layer; any safety patterns we're missing in `trading/risk.py` |
| `polymarket-assistant-tool` | How they fuse Binance order flow with Polymarket prices for signal generation | New signal source in `engine/probability.py` — order flow divergence signal |
| `polymarket-agents` | Gamma API usage patterns, Chroma vector DB for news vectorization, LLM-based probability estimation | Potential news sentiment signal; better market discovery in `market/clob_monitor.py` |

After scanning, write a `recon_notes.md` summarizing: (1) what's worth stealing, (2) what's irrelevant, (3) specific integration points into our codebase.

---

## PHASE 2: NEW SIGNALS — Bolt Into the Bayesian Engine

Each new signal follows the existing pattern: `log_odds += weight × strength × confidence`. Implement as new functions that `engine/probability.py` or `engine/bayesian.py` calls.

### Signal A: Pre-Resolution Flatline Detector
**Hypothesis:** Markets where price doesn't move for 48+ hours before resolution resolve to the leading side ~79-81% of the time. Whales have already priced in the outcome; price stagnation = settled money.

**Implementation:**
1. New module: `engine/flatline.py`
2. For every market within 72 hours of `end_date`:
   - Pull price history from our stored data (or CLOB API trade history)
   - Compute max price range over last 48 hours
   - Define "flatline" as: `max(price_48h) - min(price_48h) < threshold` (test 0.01, 0.02, 0.03)
   - If flatline detected AND leading side price > 0.60:
     - Signal strength = `(leading_price - 0.50) × 2` (normalized 0-1)
     - Signal confidence = based on how flat (tighter range = higher confidence)
3. Integrate into Bayesian engine with initial weight 0.20 (calibrate later)
4. **Must backtest first** using `fills.jsonl` resolved outcomes + historical price data

**Backtest requirements before going live:**
- Minimum 50 qualifying markets
- Win rate > 60%
- Report: win rate, avg payout, max drawdown, p-value vs random
- Log results in `discoveries.md`

### Signal B: Order Book Imbalance
**Hypothesis:** Persistent order book skew (bid depth >> ask depth or vice versa) predicts short-term price direction.

**Implementation:**
1. New module: `engine/orderbook_imbalance.py`
2. Via CLOB API `get_order_book()`: compute `bid_depth_ratio = total_bid_size / total_ask_size`
3. Signal fires when ratio > 2.5 or < 0.4 (sustained over 3+ consecutive readings, 15min apart)
4. Strength = `log(ratio)` normalized
5. Direction = toward heavier side
6. Weight: 0.10 initially (this is noisy, keep it low)
7. Integrate into existing signal array in `engine/probability.py`

### Signal C: Volume-Price Divergence
**Hypothesis:** Volume spike without corresponding price movement = accumulation. Price will follow volume direction.

**Implementation:**
1. New module: `engine/volume_divergence.py`
2. Track rolling 24h volume per market from CLOB data
3. Detect: current_volume > 2× rolling_avg AND abs(price_change_24h) < 2%
4. Determine buy/sell bias from trade tape (if available) or order flow direction
5. Strength = `(volume / rolling_avg - 1)` normalized
6. Weight: 0.10 initially
7. Integrate alongside existing signals

### Signal D: Cross-Platform Sentiment (if API keys available)
**Hypothesis:** Social/news sentiment diverging from current market price = leading indicator.

**Implementation:**
1. Study how `polymarket-agents` repo uses Chroma + news APIs
2. If I provide API keys: build `feeds/sentiment.py` following existing feed pattern
3. Per-market sentiment score: bullish/bearish/neutral
4. Integrate as signal with weight 0.05-0.10
5. **This is lowest priority** — only build if Signals A-C are integrated and backtested

---

## PHASE 3: EXPAND MARKET COVERAGE

The bot currently skips entire categories. Some of these have exploitable inefficiencies.

### 3A: Expand Contract Parser
Update `engine/contract_parser.py` to handle:

| New Category | Example | Model Approach |
|-------------|---------|---------------|
| **Election/political** | "Will X win the 2026 midterm?" | Polling aggregation signal (if we add a polls feed) + flatline detector near resolution |
| **Deadline/event** | "Will X happen by March 31?" | Time-decay model + news sentiment. These are where flatline pattern is strongest |
| **Crypto milestone** | "Will ETH ETF be approved by Q2?" | Existing crypto signals + regulatory news sentiment |

### 3B: Improved Market Discovery
Current `clob_monitor.py` scans up to 3,000 markets via Gamma API. Enhance:
1. Score markets by **edge opportunity**: high volume + reasonable spread + approaching resolution
2. Prioritize markets where our signal engine has data coverage (crypto/macro first, expand from there)
3. Track market "staleness" — flag markets with declining volume (less liquid = harder to exit)
4. Add a `market_screener.py` that outputs a ranked watchlist every hour

---

## PHASE 4: IMPROVE EXISTING SYSTEMS

### 4A: Smarter Arbitrage Scanner
Current `arb_scanner.py` detects monotonicity violations in threshold chains. Enhance:
1. **Cross-temporal arbitrage:** "BTC > $100k by March" should never exceed "BTC > $100k by June" — detect violations across different expiry dates for same asset/target
2. **Complement arbitrage:** YES + NO prices should sum to ~$1.00. Flag markets where sum deviates by >3% (after fees)
3. **Correlated market arbitrage:** If "Fed cuts rates in April" = 80% and "Fed cuts rates in 2026" = 40%, that's inconsistent. Detect these logical contradictions across related markets
4. When violations found: calculate exact arb profit after fees. Only flag if net profit > 1%

### 4B: Dynamic Signal Weights
Current weights are static in `config.py`. Enhance:
1. New module: `calibration/weight_optimizer.py`
2. After accumulating 100+ resolved trades in `fills.jsonl`:
   - Run logistic regression: `outcome ~ signal_1 + signal_2 + ... + signal_n`
   - Extract optimized weights per signal
   - Compare optimized weights vs current static weights
   - Log recommendations in `discoveries.md`
3. Don't auto-update weights yet — output recommendations for manual review
4. Eventually: add a `--optimize-weights` flag that updates `config.py` from historical data

### 4C: Improved Calibration
Current calibration tracks Brier score and calibration curve. Enhance:
1. **Per-signal attribution:** Which signals contribute most to winning trades? Which are noise?
2. **Time-decay analysis:** Do signals degrade as markets approach resolution? Some might be early-stage only
3. **Category breakdown:** Brier score per market category (crypto vs macro vs rates)
4. **Edge decay monitoring:** Alert if rolling 30-day edge drops below 2% — signals might be getting arbitraged away
5. New report: `python -m calibration.metrics --detailed` that outputs all of the above

### 4D: Execution Improvements
1. **Stale order cleanup:** If an open order hasn't filled in 30 minutes and price has moved >2%, cancel and re-price
2. **Smart order splitting:** For positions >$30, split into 2-3 smaller orders at slightly different prices to improve fill rate
3. **Resolution auto-redeem:** After market resolves, automatically call `redeemPositions` to collect USDC
4. **Heartbeat safety:** If CLOB WebSocket disconnects for >60 seconds, cancel all open orders immediately

---

## PHASE 5: MONITORING & OBSERVABILITY

### 5A: Terminal Dashboard
Build `monitoring/dashboard.py` using `rich` or `textual`:
- Live P&L (today, 7-day, 30-day, all-time)
- Open positions with current market price vs entry price
- Active signals firing right now
- Feed health status (which feeds are connected/stale)
- Last 10 trades with outcome
- Flatline detector: markets currently in flatline state

### 5B: Alert System
Build `monitoring/alerts.py`:
- Telegram bot (or Discord webhook) notifications for:
  - Trade executed (paper or live)
  - Arb opportunity detected
  - Daily P&L summary
  - Feed disconnection
  - Risk limit approaching (>80% of daily loss limit)
  - Flatline pattern detected on high-volume market

---

## IMPLEMENTATION RULES

1. **Never break existing tests.** Run `pytest` after every change. If a test breaks, fix it before moving on.
2. **Add tests for every new module.** Match the existing test density (~15 tests per module). New signals need: unit tests for the signal function, integration test with the Bayesian engine, edge case tests (missing data, zero volume, etc.)
3. **Follow existing code patterns.** Study how current signals are structured in `engine/probability.py` before adding new ones. Match the function signatures, return types, and logging style.
4. **Config goes in `config.py`.** New weights, thresholds, and toggles belong there, not hardcoded in modules.
5. **Everything logs to `fills.jsonl`.** New signals must record their readings alongside every trade signal for later calibration analysis.
6. **Paper mode must work perfectly.** All new features must function in paper mode. Never skip paper testing.
7. **Document in `discoveries.md`.** Every backtest result, every new pattern found, every weight recommendation — log it with date, sample size, and confidence level.

---

## PRIORITY ORDER

Do these in sequence. Each phase depends on the previous:

```
1. Reconnaissance (scan repos, write recon_notes.md)          ~ 30 min
2. Flatline signal (highest expected edge, backtest first)     ~ 2 hours  
3. Order book imbalance signal                                 ~ 1 hour
4. Volume-price divergence signal                              ~ 1 hour
5. Improved arb scanner (cross-temporal + complement)          ~ 1 hour
6. Dynamic weight optimizer                                    ~ 1 hour
7. Enhanced calibration reports                                ~ 1 hour
8. Market coverage expansion (parser + screener)               ~ 2 hours
9. Execution improvements                                      ~ 1 hour
10. Dashboard + alerts                                         ~ 2 hours
```

**After each new signal:** run full test suite, run backtest if historical data available, update `discoveries.md`.

---

## SUCCESS CRITERIA

The enhancement mission is successful when:

- [ ] 3+ new signals integrated into Bayesian engine, each backtested
- [ ] Flatline pattern validated or invalidated with real data (either outcome is valuable)
- [ ] Arb scanner catches cross-temporal and complement violations
- [ ] `discoveries.md` has 5+ documented patterns with statistics
- [ ] All existing 222 tests still pass + 50+ new tests added
- [ ] Weight optimization report generated from `fills.jsonl` data
- [ ] Per-signal attribution analysis completed
- [ ] Market screener ranks opportunities by edge potential
- [ ] Paper mode exercises all new features end-to-end

**Start with the recon. Then flatline. Ship working code, not plans.**
