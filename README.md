# Polymarket Bot v2.1

Automated prediction market trading bot for [Polymarket](https://polymarket.com). Targets crypto price prediction markets using a multi-factor Bayesian signal engine with conservative Kelly sizing.

---

## Architecture

```
Feeds (Deribit, Binance, Glassnode, Yahoo) → AppState
Gamma API → CLOBMonitor → AppState
AppState → trading_loop → fills.jsonl
```

### Modules

| Path | Purpose |
|------|---------|
| `main.py` | Async orchestrator — gathers all feeds + trading loop |
| `config.py` | Central constants (Kelly, EV threshold, signal weights, fees) |
| `market/state.py` | `AppState` + `ContractState` + `FeedState` (shared mutable state) |
| `market/clob_monitor.py` | Gamma API discovery → CLOB WebSocket live price updates |
| `engine/contract_parser.py` | Parse market question → asset/direction/target/expiry |
| `engine/bayesian.py` | Logit-additive Bayesian engine; `log_odds += weight × strength × confidence` |
| `engine/probability.py` | TTE-aware log-normal model; builds 6 signals; returns `(prob, count, engine)` |
| `engine/signal_filter.py` | Requires ≥2 signals AND ≥60% directional agreement |
| `feeds/deribit.py` | Deribit WebSocket — DVOL (BTC/ETH) + 25-delta vol skew |
| `feeds/microstructure.py` | Binance FAPI — funding rate + open interest (60s poll) |
| `feeds/onchain.py` | Glassnode — netflow, SOPR, NUPL, whale ratio, SSR (requires paid API) |
| `feeds/macro.py` | Yahoo Finance — DXY, 10Y yield; confidence decays on staleness |
| `trading/ev_gate.py` | EV = `(prob × payout) - (price + fee + spread + adverse_selection)`; BUY_YES or BUY_NO |
| `trading/kelly.py` | Conservative Kelly: 5% fraction, hard cap $50/trade |
| `trading/risk.py` | Daily loss limit, position cap, direction-bucketed group exposure (25%), DVOL regime |
| `trading/slippage.py` | Rejects volume <$10k; slippage_pct = min(size/vol × 0.10, 10%) |
| `trading/executor.py` | Paper (default) or live CLOB order placement via `py-clob-client` |
| `calibration/tracker.py` | Logs every signal to `fills.jsonl`; records outcome at resolution |
| `calibration/metrics.py` | Brier score, calibration curve, mean edge |

---

## Setup

```bash
git clone <repo>
cd polymarket-bot
pip install -r requirements.txt
cp .env.template .env
# Fill in .env (see Environment Variables below)
python main.py
```

---

## Environment Variables

<!-- AUTO-GENERATED from .env.template -->
| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `BANKROLL_USDC` | No | Trading bankroll in USDC | `500` |
| `PAPER` | No | Paper mode — no real orders | `true` |
| `POLY_PRIVATE_KEY` | Yes | Polygon wallet private key (`0x...`) | — |
| `POLY_API_KEY` | Yes | Polymarket CLOB API key | — |
| `GLASSNODE_API_KEY` | No | Glassnode API key (paid tier required). If absent, on-chain feed is inactive | — |
<!-- END AUTO-GENERATED -->

---

## Running

```bash
# Paper mode (default — safe to run)
python main.py

# Live mode (only after paper validation checklist passes)
PAPER=false python main.py

# Run tests
pytest

# Debug market discovery
python debug_clob.py
```

---

## Signal Engine

Six signals feed the Bayesian engine via logit addition:

| Signal | Weight | Source | Notes |
|--------|--------|--------|-------|
| `dvol_lognormal` | 0.30 | Deribit DVOL | Log-normal P(S_T > target); confidence scales with TTE |
| `vol_skew` | 0.15 | Deribit options chain | 25-delta put IV − call IV |
| `funding_rate` | 0.15 | Binance FAPI | Positive = long-biased market |
| `onchain_netflow` | 0.10 | Glassnode | Exchange outflow = bullish |
| `macro_dxy` | 0.10 | Yahoo Finance | DXY inverse correlation to BTC |
| `fed_cut_prob` | 0.10 | Yahoo Finance | Rates markets only |

Signal filter: **≥2 signals AND ≥60% directional agreement** required before EV check.

---

## Trade Entry Logic

```
parse_contract → build_model_probability → passes_signal_filter
→ estimate_slippage → calculate_ev → should_enter
→ fractional_kelly → risk.can_trade → executor.place_order
```

- **BUY_YES**: model_prob > market_ask (market underpricing YES)
- **BUY_NO**: 1 − model_prob > 1 − market_bid (market overpricing YES)
- **EV threshold**: 3% minimum after fees (2%) + spread penalty + adverse selection (0.5%)
- **Kelly**: 5% fraction × EV confidence × signal confidence, hard cap $50

---

## Paper Validation Checklist

Do NOT set `PAPER=false` until:

- [ ] 50+ signals logged in `fills.jsonl`
- [ ] 20+ resolved outcomes recorded
- [ ] Brier score < 0.20
- [ ] Mean edge > 3%
- [ ] No single day exceeds 5% bankroll loss
- [ ] Calibration curve reviewed (`python -c "from calibration.metrics import print_calibration_report; print_calibration_report('fills.jsonl')"`)

---

## Phase 2 (deferred — do not build until 50+ resolved signals)

- `engine/kl_scanner.py` — cross-contract KL divergence mispricing detector
- `tracking/reconciler.py` — position reconciliation + drift alerts
- FedWatch HTML scraper (currently returns 0.5 placeholder)
- Term structure and IV/RV spread (need price history in AppState)

---

## Tests

143 tests across 11 modules. All passing.

```bash
pytest                    # run all
pytest tests/engine/      # single module
pytest -v                 # verbose
```
