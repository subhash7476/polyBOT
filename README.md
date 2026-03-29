# Polymarket Bot v3.0

Automated prediction market trading bot for [Polymarket](https://polymarket.com).
Targets crypto price markets, Fed rate cut markets, macro data-release markets,
election markets, and deadline/event markets using a multi-factor Bayesian signal
engine with conservative Kelly sizing.

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
| `main.py` | Async orchestrator — all feeds + trading loop + arb scanner + alert hooks |
| `config.py` | Central constants (Kelly, EV threshold, signal weights, fees, flatline/OBI/VPD thresholds) |
| `market/state.py` | `AppState` + `ContractState` (neg_risk, fees_enabled, bid_depth, ask_depth) + `FeedState` |
| `market/clob_monitor.py` | Gamma API discovery (3,000 markets) + weather events API (`tag_slug=weather`) → CLOB WebSocket prices + depth |
| `market/market_screener.py` | Ranks active markets by edge opportunity (volume, spread, expiry, parseability) |
| `engine/contract_parser.py` | Parse question → asset/direction/target/expiry/cut_count; categories: crypto/rates/macro/election/event/weather |
| `engine/bayesian.py` | Logit-additive engine: `log_odds += weight × strength × confidence` |
| `engine/probability.py` | Lognormal GBM prior per asset; 7 feed signals; returns `(prob, count, engine)` |
| `engine/macro_probability.py` | Normal forecast-error model (CPI/GDP/NFP); Poisson model (rate cut counts) |
| `engine/weather_probability.py` | Normal bucket model for temperature markets; blends METAR + ECMWF + HRRR with time-to-resolution sigma scaling |
| `engine/flatline.py` | Pre-resolution price stagnation signal (48h range < 2c within 72h of expiry; weight=0.20) |
| `engine/orderbook_imbalance.py` | Bid/ask depth ratio signal (>2.5 or <0.4 sustained 3+ readings; weight=0.10) |
| `engine/volume_divergence.py` | Volume spike without price move signal (volume >2× rolling avg, price <2% drift; weight=0.10) |
| `engine/arb_scanner.py` | Monotonicity violation + cross-temporal arb detector for threshold market chains |
| `engine/signal_filter.py` | ≥2 signals + ≥60% agreement; OR decisive prior (≥40% confidence) + 1 signal |
| `feeds/deribit.py` | Deribit WebSocket — DVOL (BTC/ETH/SOL) + 25-delta vol skew |
| `feeds/microstructure.py` | Binance FAPI — spot price + funding rate for 8 assets (60s poll) |
| `feeds/onchain.py` | DeFiLlama stablecoin supply + Blockchain.com hash rate (free, no key) |
| `feeds/macro.py` | FRED + NY Fed SOFR — CPI YoY, unemployment, target rate, Poisson lambda (curl fallback for TLS) |
| `feeds/weather.py` | ECMWF open-meteo + METAR observations for 34 cities; multi-day forecast dicts; parallelized refresh |
| `feeds/weather_types.py` | `WeatherForecast` dataclass — ecmwf_by_date, hrrr_by_date, metar_temp per city |
| `trading/ev_gate.py` | EV = `(prob × payout) - (price + fee + adverse_selection)`; fee=0 for negRisk weather markets |
| `trading/kelly.py` | 5% fractional Kelly; hard cap $50/trade |
| `trading/risk.py` | Daily loss limit; position cap (default 20); direction-bucketed group exposure (20%) |
| `trading/slippage.py` | Rejects volume <$10k or spread >15c; linear market-impact model |
| `trading/executor.py` | Paper (default) or live CLOB order via `py-clob-client`; stale-order + split helpers |
| `trading/redeemall.py` | Auto-redeem resolved positions every 15 min; manual `python -m trading.redeemall` |
| `calibration/tracker.py` | Logs every signal to `fills.jsonl`; records outcome at resolution |
| `calibration/metrics.py` | Brier score, calibration curve, mean edge; `--detailed` flag adds per-signal attribution |
| `calibration/weight_optimizer.py` | Logistic regression on `fills.jsonl` → weight recommendations (never auto-applies) |
| `monitoring/alerts.py` | Telegram alerts for trades, arb, risk limit, flatline, feed disconnection |
| `dashboard/` | Live terminal dashboard (P&L, open positions, feed health, scan stats) |
<!-- END AUTO-GENERATED -->

---

## Setup

```bash
git clone <repo>
cd polymarket-bot
pip install -r requirements.txt
cp .env.template .env  # fill in credentials
python main.py
```

---

## Environment Variables

<!-- AUTO-GENERATED from .env.template + config.py -->
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POLY_PRIVATE_KEY` | Yes (live) | — | `0x`-prefixed EOA private key |
| `SIGNATURE_TYPE` | No | `0` | `0`=EOA, `1`=POLY_PROXY (Magic Link), `2`=GNOSIS_SAFE (MetaMask) |
| `FUNDER_ADDRESS` | If type 1/2 | — | Proxy wallet address |
| `RPC_URL` | No | `https://polygon-rpc.com` | Polygon RPC (Alchemy/Ankr recommended) |
| `BANKROLL_USDC` | No | `500` | Total bankroll for Kelly sizing |
| `PAPER` | No | `true` | Set `false` only after paper validation checklist passes |
| `MAX_TRADE_SIZE_USDC` | No | `50` | Hard cap per trade |
| `MAX_OPEN_POSITIONS` | No | `20` | Max concurrent open positions |
| `POLY_API_KEY` | Yes (live) | — | Polymarket CLOB API key |
| `POLY_API_SECRET` | Yes (live) | — | Polymarket CLOB API secret |
| `POLY_API_PASSPHRASE` | Yes (live) | — | Polymarket CLOB API passphrase |
| `TELEGRAM_BOT_TOKEN` | No | — | Bot token from @BotFather — enables alerts + remote control |
| `TELEGRAM_CHAT_ID` | No | — | Your Telegram user/chat ID |
| `FRED_API_KEY` | No | — | Adds consensus CPI/GDP/unemployment detail; basic data works without it |
| `GLASSNODE_API_KEY` | No | — | On-chain netflow; falls back to DeFiLlama + Blockchain.com if absent |
| `WEATHER_POLL_INTERVAL` | No | `3600` | Seconds between weather feed refreshes |
<!-- END AUTO-GENERATED -->

---

## Running

```bash
# Paper mode (default — safe, no real money)
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

# Calibration report
python -m calibration.metrics
python -m calibration.metrics --detailed   # per-signal attribution, category breakdown, edge decay

# Signal weight optimizer (requires 100+ resolved fills)
python -m calibration.weight_optimizer

# Market screener (ranked watchlist)
python -m market.market_screener

# Redeem resolved positions manually
python -m trading.redeemall

# Set on-chain allowances (run once before live trading)
python -m setup.allowances

# Telegram remote control (separate process)
python telegram_bot.py
```

---

## Signal Engine

### Crypto contracts (BTC, ETH, SOL, XRP, BNB, DOGE, ADA, AVAX)

Lognormal GBM prior anchors the model; signals adjust via logit addition:

| Signal | Weight | Source | Notes |
|--------|--------|--------|-------|
| Lognormal prior | — | Deribit DVOL | `P(S_T > target)` under GBM; falls back to 0.5 if DVOL unavailable |
| `vol_skew` | 0.15 | Deribit options | 25-delta put IV minus call IV |
| `funding_rate` | 0.15 | Binance FAPI | Positive = crowded longs = mean-revert pressure |
| `onchain_netflow` | 0.10 | Glassnode (paid) | Exchange outflow = bullish |
| `macro_dxy` | 0.10 | Yahoo Finance | DXY inverse correlation to crypto |
| `stablecoin_supply` | 0.05 | DeFiLlama (free) | Rising supply = new money entering |
| `btc_hashrate` | 0.05 | Blockchain.com (free) | BTC only; rising hash rate = miner confidence |
| `flatline` | 0.20 | CLOB price history | 48h price range <2c within 72h of expiry → signals to leading side (all categories) |
| `orderbook_imbalance` | 0.10 | CLOB order book | Bid/ask depth ratio >2.5 or <0.4 sustained 3+ readings (all categories) |
| `volume_divergence` | 0.10 | CLOB trade data | Volume >2× rolling avg without proportional price move (all categories) |

### Weather markets ("Will the highest temp in [city] be X°C on [date]?")

Normal bucket probability: `P(lo ≤ T_max ≤ hi)` using blended forecast as mean.

Forecast blend:
- **US cities**: METAR (0.5) + HRRR (0.3) + ECMWF (0.2) when METAR available; HRRR (0.6) + ECMWF (0.4) otherwise
- **Non-US cities**: METAR (0.5) + ECMWF (0.5) when METAR available; ECMWF only otherwise

Sigma scales with time-to-resolution: D+0 (<12 h) × 0.6, D+1 × 1.0, D+2+ × 1.2.

| Signal | Weight | Source |
|--------|--------|--------|
| `weather_forecast_confidence` | 0.35 | ECMWF open-meteo + METAR blend |
| `weather_forecast_agreement` | 0.25 | ECMWF vs METAR agreement |

Markets discovered via Polymarket Events API (`tag_slug=weather`). All are negRisk AMM markets; fee = 0%.

---

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
| `crypto` | "Will BTC hit $150k by Dec 31?" | Lognormal GBM + microstructure signals |
| `rates` (count) | "Will 2 Fed rate cuts happen in 2026?" | Poisson(lambda) |
| `rates` (meeting) | "Will the Fed cut 25 bps at the April meeting?" | `fed_may_cut_prob` signal |
| `macro` | "Will CPI exceed 3.5% in Q1?" | Normal forecast-error |
| `weather` | "Will the highest temp in Wellington be 19°C on Mar 29?" | Normal bucket model; METAR+ECMWF blend; fee=0 (negRisk) |
| `election` | "Will Democrats win the 2026 midterm?" | Flatline signal near resolution |
| `event` | "Will the ETH ETF be approved by Q2?" | Flatline signal + time-decay |

All categories receive flatline, order-book imbalance, and volume-divergence signals.

Weather markets are discovered via `events?tag_slug=weather` (not standard market pagination); all are negRisk AMM markets with no trading fee.

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

Every 30 seconds, `arb_scan_loop` checks:
1. **Monotonicity violations** — `P(BTC > $80k) ≥ P(BTC > $85k) ≥ P(BTC > $90k)` within a single expiry
2. **Cross-temporal violations** — `P(BTC > $100k by Mar) ≤ P(BTC > $100k by Jun)` across expiry dates

Violations with sufficient spread (>1% net of fees) logged to `fills.jsonl` and sent as Telegram alerts (spread >5%).

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

449 tests across 25 modules. All passing.

```bash
pytest                                         # run all
pytest tests/engine/                           # engine tests (parser, Bayesian, arb, signals)
pytest tests/feeds/                            # feed tests
pytest tests/trading/                          # trading tests
pytest tests/calibration/                      # calibration + weight optimizer
pytest tests/market/                           # screener + depth tracking
pytest tests/monitoring/                       # alert system
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
| Polymarket Events API (`tag_slug=weather`) | Temperature bucket market discovery (negRisk) | `market/clob_monitor.py` |
| open-meteo ECMWF API | 3-day temperature forecast, 34 cities | `feeds/weather.py` |
| aviationweather.gov METAR | Current observed temperature (airport stations) | `feeds/weather.py` |
