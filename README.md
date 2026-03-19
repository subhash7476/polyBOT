# Polymarket Bot v3.0

Automated prediction market trading bot for [Polymarket](https://polymarket.com).
Targets crypto price markets, Fed rate cut markets, and macro data-release markets
using a multi-factor Bayesian signal engine with conservative Kelly sizing.

---

## Architecture

```
Feeds (Deribit, Binance, DeFiLlama, Blockchain.com, FRED, NY Fed, Yahoo)
  -> AppState (FeedState + ContractState)
Gamma API -> CLOBMonitor -> AppState
AppState -> trading_loop + arb_scan_loop -> fills.jsonl
```

### Modules

<!-- AUTO-GENERATED -->
| Path | Purpose |
|------|---------|
| `main.py` | Async orchestrator -- all feeds + trading loop + arb scanner |
| `config.py` | Central constants (Kelly, EV threshold, signal weights, fees) |
| `market/state.py` | `AppState` + `ContractState` + `FeedState` (per-asset dicts + macro scalars) |
| `market/clob_monitor.py` | Gamma API discovery (up to 3,000 markets) -> CLOB WebSocket live prices |
| `engine/contract_parser.py` | Parse question -> asset/direction/target/expiry/cut_count |
| `engine/bayesian.py` | Logit-additive engine: `log_odds += weight x strength x confidence` |
| `engine/probability.py` | Lognormal GBM prior per asset; 7 signals; returns `(prob, count, engine)` |
| `engine/macro_probability.py` | Normal forecast-error model (CPI/GDP/NFP); Poisson model (rate cut counts) |
| `engine/arb_scanner.py` | Monotonicity violation detector for threshold market chains |
| `engine/signal_filter.py` | >=2 signals + >=60% agreement; OR decisive prior (>=40% confidence) + 1 signal |
| `feeds/deribit.py` | Deribit WebSocket -- DVOL (BTC/ETH/SOL) + 25-delta vol skew |
| `feeds/microstructure.py` | Binance FAPI -- spot price + funding rate for 8 assets (60s poll) |
| `feeds/onchain.py` | DeFiLlama stablecoin supply + Blockchain.com hash rate (free, no key) |
| `feeds/macro.py` | FRED + NY Fed SOFR -- CPI YoY, unemployment, target rate, Poisson lambda |
| `trading/ev_gate.py` | EV = `(prob x payout) - (price + fee + adverse_selection)`; BUY_YES/BUY_NO |
| `trading/kelly.py` | 5% fractional Kelly; hard cap $50/trade |
| `trading/risk.py` | Daily loss limit; position cap; direction-bucketed group exposure (20%) |
| `trading/slippage.py` | Rejects volume <$10k or spread >15c; linear market-impact model |
| `trading/executor.py` | Paper (default) or live CLOB order via `py-clob-client` |
| `calibration/tracker.py` | Logs every signal to `fills.jsonl`; records outcome at resolution |
| `calibration/metrics.py` | Brier score, calibration curve, mean edge |
<!-- END AUTO-GENERATED -->

---

## Setup

```bash
git clone <repo>
cd polymarket-bot
pip install -r requirements.txt
cp .env.example .env   # fill in credentials
python main.py
```

---

## Environment Variables

<!-- AUTO-GENERATED from config.py -->
| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `BANKROLL_USDC` | No | Trading bankroll in USDC | `500` |
| `PAPER` | No | Paper mode -- no real orders sent | `true` |
| `POLY_PRIVATE_KEY` | Yes (live) | Polygon wallet private key (`0x...`) | -- |
| `POLY_API_KEY` | Yes (live) | Polymarket CLOB API key | -- |
| `FRED_API_KEY` | No | FRED API key for macro consensus forecasts (CPI/GDP/NFP/unemployment). Basic rate/CPI data fetched without key via free CSV endpoints; full consensus detail requires key | -- |
| `GLASSNODE_API_KEY` | No | Glassnode API key (paid). If absent, on-chain feed uses free DeFiLlama + Blockchain.com sources | -- |
<!-- END AUTO-GENERATED -->

---

## Running

```bash
# Paper mode (default -- safe, no real money)
python main.py

# Live mode (only after paper validation checklist passes)
PAPER=false python main.py

# Run tests
pytest

# Module-level tests
pytest tests/engine/
pytest tests/feeds/
pytest tests/trading/
pytest tests/test_integration_multi_asset.py

# Verbose
pytest -v
```

---

## Signal Engine

### Crypto contracts (BTC, ETH, SOL, XRP, BNB, DOGE, ADA, AVAX)

Lognormal GBM prior anchors the model; signals adjust via logit addition:

| Signal | Weight | Source | Notes |
|--------|--------|--------|-------|
| Lognormal prior | -- | Deribit DVOL | `P(S_T > target)` under GBM; falls back to 0.5 if DVOL unavailable |
| `vol_skew` | 0.15 | Deribit options | 25-delta put IV minus call IV |
| `funding_rate` | 0.15 | Binance FAPI | Positive = crowded longs = mean-revert pressure |
| `onchain_netflow` | 0.10 | Glassnode (paid) | Exchange outflow = bullish |
| `macro_dxy` | 0.10 | Yahoo Finance | DXY inverse correlation to crypto |
| `stablecoin_supply` | 0.05 | DeFiLlama (free) | Rising supply = new money entering |
| `btc_hashrate` | 0.05 | Blockchain.com (free) | BTC only; rising hash rate = miner confidence |

### Rate cut count markets ("Will N Fed rate cuts happen in 2026?")

Poisson model: `P(exactly N) = Poisson(N, lambda)` or `P(>=N) = 1 - CDF(N-1, lambda)`

Lambda computed from FRED CPI YoY + unemployment via Taylor-rule heuristic.
SOFR from NY Fed provides a second confirming signal.

| Signal | Weight | Source |
|--------|--------|--------|
| `poisson_cut_model` | 0.30 | FRED CPI YoY + UNRATE (free CSV) |
| `sofr_cut_signal` | 0.15 | NY Fed SOFR API (free) |

### Macro data-release markets (CPI, GDP, NFP, unemployment)

Normal forecast-error model: `P(actual > target) = 1 - phi((target - consensus) / sigma)`

Requires `FRED_API_KEY` for consensus forecasts.

Signal filter: **>=2 signals + >=60% directional agreement**, OR **decisive prior (>=40% confidence from lognormal/Poisson) + 1 confirming signal**.

---

## Supported Market Types

| Category | Examples | Model |
|----------|---------|-------|
| `crypto` | "Will BTC hit $150k by Dec 31?" | Lognormal GBM |
| `rates` (count) | "Will 2 Fed rate cuts happen in 2026?" | Poisson(lambda) |
| `rates` (meeting) | "Will the Fed cut 25 bps at the April meeting?" | `fed_may_cut_prob` signal |
| `macro` | "Will CPI exceed 3.5% in Q1?" | Normal forecast-error |

Skipped: FDV/market-cap questions, token launch markets, sports, entertainment.

---

## Trade Entry Logic

```
parse_contract -> build_model_probability (or build_macro_probability)
-> passes_signal_filter -> estimate_slippage
-> calculate_ev -> should_enter
-> fractional_kelly -> risk.can_trade -> executor.place_order
```

- **BUY_YES**: model_prob > market_mid -- YES is underpriced
- **BUY_NO**: 1 - model_prob > 1 - market_mid -- NO is underpriced
- **EV threshold**: 3% minimum after fees (2%) + adverse selection (0.5%)
- **Kelly**: 5% fraction, hard cap $50/trade

### Arbitrage Scanner

Every 30 seconds, `arb_scan_loop` checks monotonicity across threshold chains:
`P(BTC > $80k) >= P(BTC > $85k) >= P(BTC > $90k)`. Violations logged to `fills.jsonl`.

---

## Paper Validation Checklist

Do **not** set `PAPER=false` until:

- [ ] 50+ signals logged in `fills.jsonl`
- [ ] 20+ resolved outcomes recorded
- [ ] Brier score < 0.20
- [ ] Mean edge > 3%
- [ ] No single day exceeds 5% bankroll loss
- [ ] Calibration curve reviewed:
  ```bash
  python -c "from calibration.metrics import print_calibration_report; print_calibration_report('fills.jsonl')"
  ```

---

## Tests

222 tests across 15 modules. All passing.

```bash
pytest                                         # run all
pytest tests/engine/                           # engine tests
pytest tests/feeds/                            # feed tests
pytest tests/trading/                          # trading tests
pytest tests/test_integration_multi_asset.py   # end-to-end integration
pytest -v                                      # verbose
```

---

## Free Data Sources (no API key required)

| Source | Data | Feed |
|--------|------|------|
| Binance FAPI `/premiumIndex` | Spot prices + funding rates, 8 assets | `feeds/microstructure.py` |
| Deribit WebSocket | DVOL (BTC/ETH/SOL) + vol skew | `feeds/deribit.py` |
| DeFiLlama `/stablecoinchains` | Total stablecoin supply, 175 chains | `feeds/onchain.py` |
| Blockchain.com `/charts/hash-rate` | 30-day BTC hash rate trend | `feeds/onchain.py` |
| FRED CSV (`DFEDTARL`, `DFEDTARU`, `CPIAUCSL`, `UNRATE`) | Fed target bounds, CPI index, unemployment | `feeds/macro.py` |
| NY Fed SOFR API | Overnight SOFR rate | `feeds/macro.py` |
| Yahoo Finance | DXY, 10Y yield | `feeds/macro.py` |
| Polymarket Gamma API | Market discovery, up to 3,000 markets | `market/clob_monitor.py` |
