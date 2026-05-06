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
- Calibration tracker: Brier score, calibration curve, mean edge → fills.jsonl- Contract parser: asset/direction/target/expiry/cut_count extraction

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
| `polymarket-mcp-server` | Their 45-tool structure, safety guardrails, real-time monitoring patterns | Better monitoring in our `calibration/` layer |
| `polymarket-assistant-tool` | How they fuse Binance order flow with Polymarket prices for signal generation | New signal source in `engine/probability.py` |
| `polymarket-agents` | Gamma API usage, Chroma vector DB for news, LLM probability estimation | Better market discovery in `market/clob_monitor.py` |
After scanning, write a `recon_notes.md` summarizing: (1) what's worth stealing, (2) what's irrelevant, (3) specific integration points.

---

## PHASE 2: NEW SIGNALS — Bolt Into the Bayesian Engine

Each new signal follows: `log_odds += weight × strength × confidence`. Implement as new functions in `engine/probability.py` or `engine/bayesian.py`.

### Signal A: Pre-Resolution Flatline Detector
- New module: `engine/flatline.py`
- Markets within 72h of end_date, price range <threshold over 48h
- Signal strength = `(leading_price - 0.50) × 2`; weight 0.20
- Backtest first: need 50+ qualifying markets, win rate >60%

### Signal B: Order Book Imbalance
- New module: `engine/orderbook_imbalance.py`
- Bid/ask depth ratio >2.5 or <0.4, sustained 3+ readings
- Weight: 0.10

### Signal C: Volume-Price Divergence
- New module: `engine/volume_divergence.py`
- Volume >2× rolling avg AND price change <2%
- Weight: 0.10

### Signal D: Cross-Platform Sentiment (lowest priority)
- Study polymarket-agents Chroma + news approach
- Weight: 0.05-0.10

---

## PHASE 3-5: See full prompt for market expansion, system improvements, monitoring.

**Start with recon. Then flatline. Ship working code, not plans.**