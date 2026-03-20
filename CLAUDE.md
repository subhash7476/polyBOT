# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
pytest

# Run a specific module's tests
pytest tests/engine/
pytest tests/feeds/
pytest tests/trading/
pytest tests/test_integration_multi_asset.py

# Run a single test file or test
pytest tests/engine/test_bayesian.py
pytest tests/engine/test_bayesian.py::test_signal_adds_log_odds -v

# Start the bot (paper mode by default)
python main.py

# Check calibration metrics after paper trading
python -c "from calibration.metrics import print_calibration_report; print_calibration_report('fills.jsonl')"

# Run FTMO backtest (separate system in ftmo/)
python -m ftmo.cli backtest

# Run Telegram bot (separate process)
python telegram_bot.py

# Set on-chain allowances (run once before live trading)
python -m setup.allowances

# Manually redeem all eligible positions
python -m trading.redeemall

# Manually redeem a single condition
python -m trading.redeem <condition_id>
```

## Architecture

### Signal Pipeline (trading_loop, every 5s)

```
CLOBMonitor (AppState.markets)
  → parse_contract()          [engine/contract_parser.py]
  → build_model_probability() [engine/probability.py] OR
    build_macro_probability()  [engine/macro_probability.py]   ← for category "rates"/"macro"
  → passes_signal_filter()    [engine/signal_filter.py]
  → estimate_slippage()       [trading/slippage.py]
  → calculate_ev()            [trading/ev_gate.py]
  → fractional_kelly()        [trading/kelly.py]
  → risk.can_trade()          [trading/risk.py]
  → executor.place_order()    [trading/executor.py]
  → tracker.log_signal()      → fills.jsonl
```

### Probability Models

**Crypto contracts** (`engine/probability.py`): Lognormal GBM prior via `lognormal_prob_above(spot, target, sigma_annual, T_days)` — computes `N(d2)` where `d2 = ln(spot/target) / sigma_T`. Initializes `BayesianEngine` with this prior, then adds feed signals (dvol, vol_skew, funding_rate, etc.) as logit adjustments.

**Rate/macro contracts** (`engine/macro_probability.py`): Routes on `contract.category`. For `"rates"`: Poisson model (`poisson.pmf(N, λ)` or survival function for "≥N") where `λ = fed_expected_cuts` from FRED CPI+unemployment Taylor rule. For `"macro"` (CPI/GDP/NFP/unemployment): Normal forecast-error model `1 - Φ((target - consensus) / sigma)`.

**BayesianEngine** (`engine/bayesian.py`): `log_odds += weight × strength × confidence` for each signal. Strength ∈ [-1, +1], confidence ∈ [0, 1] (decays when feed data is stale). Log-odds clipped ±10 before sigmoid.

### State Architecture

`AppState` holds `feeds: FeedState` and `markets: dict[str, ContractState]`, protected by `asyncio.Lock`. Feed data lives in per-asset dicts (`spot_prices["BTC"]`, `dvol["ETH"]`, etc.) not flat fields — backward-compat properties exist for `btc_price`, `eth_dvol`, etc.

Five concurrent feeds write to AppState: `DeribitFeed` (WebSocket DVOL+skew), `MicrostructureFeed` (Binance funding+spot, 60s poll), `OnChainFeed` (DeFiLlama stablecoin supply, Blockchain.com hash rate), `MacroFeed` (FRED CSV via curl fallback + NY Fed SOFR + Yahoo Finance), `CLOBMonitor` (Gamma API market discovery + Polymarket WebSocket prices).

### Risk Controls

- `RiskManager` tracks open positions bucketed by `_contract_group_key()` — returns direction-aware keys like `"btc_above"` / `"btc_below"` to prevent correlated exposure (e.g., BTC>$85k and BTC>$90k both counted against 20% group limit)
- `ev_multiplier` raises EV threshold after consecutive losses (1 loss → 1.5×, 3+ losses → 2.0×)
- Kelly: 5% fraction, hard cap $50/trade until paper validation checklist passes

### Signal Filter

`passes_signal_filter()` requires ≥2 signals with ≥60% directional agreement (same sign of strength). Exception: a strong prior (lognormal or Poisson `|prior - 0.5| × 2 ≥ 0.40`) with ≥1 confirming signal is sufficient.

### Key Config (`config.py`)

`SIGNAL_WEIGHTS` dict controls per-signal log-odds weight. `PAPER=true` by default — never set false until all 5 paper-validation checks pass (50+ signals, 20+ resolved, Brier < 0.20, mean edge > 3%, no day > 5% loss). `MAX_TRADE_SIZE_USDC = 50` hard cap.

### FRED Data

FRED CSV endpoints block httpx (TLS fingerprint). `MacroFeed._fetch_fred_csv()` tries httpx first, falls back to `curl` subprocess. Sequential fetches required — parallel requests cause FRED connection resets.

### Calibration

All evaluated signals (pass or fail) written to `fills.jsonl` with model_prob, market_prob, edge, EV, signal summary. Outcomes recorded at resolution. `calibration/metrics.py` computes Brier score + calibration curve from this log.

## Operational Infrastructure

### Log Files
All loggers write to both stdout and `logs/<name>.log`.
Rotation: every 3 hours, 24 backup files retained (3 days total).
Directory is created automatically on first run.

### Feed Staleness Gate
`FeedState.is_fresh(max_age_seconds=5.0)` — the trading loop skips a scan if
either the `clob` or `microstructure` feed has not updated within 5 seconds.
Both feeds stamp `state.stamp_feed(name)` after each successful update cycle.

### Wallet Types (`SIGNATURE_TYPE`)
- `0` (default): EOA — address derived from `POLY_PRIVATE_KEY` via `eth_account`
- `1`: POLY_PROXY — Magic Link / email registration; set `FUNDER_ADDRESS`
- `2`: POLY_GNOSIS_SAFE — MetaMask/Phantom; set `FUNDER_ADDRESS`

`CLOBExecutor.wallet_address` always holds the correct address for the configured type.

### Order Retry
`CLOBExecutor.place_order()` retries up to 3 times with 1 s delay before returning ERROR.
Constants: `_MAX_RETRIES = 3`, `_RETRY_DELAY = 1.0` in `trading/executor.py`.

### USDC Balance Tracking
`BalancePoller` (in `trading/balance.py`) polls `data-api.polymarket.com/value` every 60 s.
- Sets `BalanceState.session_start` on first successful fetch
- `session_pnl = current - session_start`
- Access via `state.balance` from any async context

### Allowance Setup (run once before live trading)
```bash
python -m setup.allowances
```
Approves CTF Exchange, NegRisk CTF Exchange, and NegRisk Adapter to spend max
bridged USDC. Requires `POLY_PRIVATE_KEY` and `RPC_URL` in `.env`.

### Redemption System
Auto-redeemall runs every 15 minutes via `redeemall_loop()` in `main.py`.

Manual:
```bash
python -m trading.redeemall          # batch — all eligible positions
python -m trading.redeem <condition_id>  # single condition
```

Flow: fetch positions from data-api → classify (active / pending / redeemable) →
verify oracle on-chain (`payoutNumerators > 0`) → call `redeemPositions()` on CTF Exchange.

`RedeemLock` (`trading/redeem_lock.py`) prevents concurrent redemptions
(e.g. auto-loop vs Telegram `/redeemall`).

### Telegram Bot
Run as a separate process:
```bash
python telegram_bot.py
```
Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
Uses raw HTTP long-polling (no `python-telegram-bot` dependency).

Commands: `/status` `/balance` `/redeemall` `/stop` `/restart` `/help`

The bot reads `bot.pid` (written by `main.py` at startup) to locate the trading process.
Stop escalation: SIGTERM → `taskkill /F` (Windows) or SIGKILL (Linux/macOS).

### Environment Variables (complete)

| Variable | Required | Default | Description |
|---|---|---|---|
| `POLY_PRIVATE_KEY` | Yes (live) | — | 0x-prefixed EOA private key |
| `SIGNATURE_TYPE` | No | `0` | `0`=EOA, `1`=POLY_PROXY, `2`=GNOSIS_SAFE |
| `FUNDER_ADDRESS` | If type 1/2 | — | Proxy wallet address |
| `RPC_URL` | No | `https://polygon-rpc.com` | Polygon RPC (Alchemy/Ankr recommended for stability) |
| `BANKROLL_USDC` | No | `500` | Total bankroll for Kelly sizing |
| `PAPER` | No | `true` | Set `false` only after paper validation checklist passes |
| `MAX_TRADE_SIZE_USDC` | No | `50` | Hard cap per trade |
| `TELEGRAM_BOT_TOKEN` | No | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | No | — | Your Telegram user/chat ID |
| `FRED_API_KEY` | No | — | Adds consensus CPI/GDP/unemployment detail |
| `GLASSNODE_API_KEY` | No | — | On-chain netflow data |

## Environment

Copy `.env.template` to `.env`. Only `POLY_PRIVATE_KEY` and `POLY_API_KEY` are needed for live mode. `FRED_API_KEY` adds consensus forecast detail (CPI/GDP/unemployment); basic rate/CPI data works without it via free CSV endpoints.
