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

## Maker Bot

Run via `python main.py` with `MODE=maker` (or directly via `maker/runner.py`). Dashboard at `http://127.0.0.1:5050/maker`.

### Actor Pipeline

```
MarketSelector  → active_markets_q  → QuoteEngine
QuoteEngine     → quote_intents_q   → OrderManager   (places/cancels ladder levels)
                → price_update_q    ← CLOBMonitor    (event-driven reprice on price ticks)
FillPoller      → fills_q           → InventoryManager
InventoryManager→ skew_updates_q    → QuoteEngine    (inventory skew adjusts fair value)
                → cancel_q          → OrderManager   (circuit breaker fires)
                → markout_q         → MarkoutTracker (post-fill price tracking)
```

### Liquidity Rewards Program

The bot earns **three stacking revenue streams** on every eligible fill:

1. **Liquidity Rewards** — daily USDC for resting orders scored by tightness (no fill needed)
2. **Maker Rebates** — 20–25% of taker fees redistributed daily on fills
3. **Spread Capture** — round-trip bid-ask P&L

**Scoring formula (quadratic):** `S(v, s) = ((v - s) / v)² × b`
- `v` = `max_incentive_spread` (market-specific, in [0,1] space, e.g. 0.03 = 3¢)
- `s` = your order's distance from mid
- Orders beyond `max_incentive_spread` score **zero**. Halving spread distance quadruples score.
- Orders below `min_incentive_size` (shares) score **zero**.

**Per-market params** are fetched automatically by `MarketSelector._fetch_incentive_params()` every 15-minute selection cycle via `GET https://clob.polymarket.com/clob-market-info?condition_id=<id>`. Stored as `ContractState.min_incentive_size` and `ContractState.max_incentive_spread`. A value of `0.0` means not yet fetched (sentinel).

**QuoteEngine enforcement:**
- `base_size` is floored at `min_incentive_size` (capped at 200sh) when the field is populated.
- A `WARNING` log is emitted (once per market) when all 3 ladder levels are outside `max_incentive_spread` — those orders score 0 for rewards.

**Maker fee rates by category:** Sports 3%, Finance/Politics 4%, Crypto 7.2%, Weather/Other 5%, Geopolitics 0% (fee-free). Maker fee = **zero** on all categories. Rebate = 20–25% of taker fees, paid daily in USDC.

**Heartbeat (`maker/runner.py`):** In live mode, `_heartbeat_loop(clob)` runs as a coroutine calling `clob.post_heartbeat()` every 30s. Prevents CLOB from auto-cancelling all open orders during network quiet periods. No-op in paper mode (`clob=None`). Failures logged as WARNING.

**WebSocket `custom_feature_enabled` (`market/clob_monitor.py`):** The subscription message includes `"custom_feature_enabled": True`, which unlocks three additional event types:
- `best_bid_ask` — handled by `_handle_best_bid_ask()`: updates `cs.best_bid`/`cs.best_ask` and nudges `price_update_q`
- `market_resolved` — handled by `_handle_market_resolved()`: logs resolution and nudges `price_update_q` so QuoteEngine's bid-range guard fires and cancels resting quotes
- `new_market` — logged at DEBUG only (discovery runs every 15 min and will pick it up)

**Reward monitoring endpoints (not yet wired to dashboard):**
- `GET /rewards/earnings` — your earnings by date (authenticated)
- `GET /rewards/percentages` — real-time reward % share
- `GET /rebates?maker=&date=` — current rebated fees

### Live-Validation Metric Layer (`maker/markout_tracker.py`)

`MarkoutTracker` is the pre-live-go validation tool. For every fill it schedules market mid samples at **T+5s, T+30s, T+60s** and computes:

```
markout = sign * (mid_later - mid_at_fill)
  where sign = +1 for BUY, -1 for SELL
```

Positive = price moved our way. Negative = adverse selection.

Results are appended to **`fills_markout.jsonl`** and aggregated into rolling stats (last 500 fills):
- `avg_markout_5s / 30s / 60s` — average post-fill price movement
- `adverse_rate_5s / 30s / 60s` — fraction of fills with negative markout
- `markout_fills` — total fills evaluated

These stats appear in the maker dashboard alongside:
- `quote_uptime` — fraction of 2s ticks with at least one quote resting
- `total_fills` / `total_cancels` — cancel-to-fill ratio (`total_cancels / total_fills`)

### Paper Mode Limitations

Paper fills use a Poisson model: `rate = volume_usd / (86400 × size × COMPETITION_FACTOR=10)`. This simulates fill probability from reported market volume but **cannot measure queue position, true fill rate, or adverse selection**. Paper mode validates plumbing; tiny live quoting validates strategy.

### Pre-Live Validation Gate (do not go live until all pass)

1. Paper mode mechanics work: quotes inside spread, inventory caps fire, circuit breakers stop exposure.
2. Tiny live quoting on 3-5 markets, minimum practical size.
3. Collect several hundred live fills.
4. `avg_markout_30s >= 0` (adverse selection manageable).
5. `adverse_rate_30s` stable and acceptable.
6. Net realized P&L after fees > 0 over multiple sessions.
7. Inventory cap hits near zero; no uncontrolled inventory drift.

### Fill Size Units

Polymarket prices are in [0,1]; positions are in **shares** (each share pays $1 at resolution). `Fill.size` is shares, not USDC notional. `price × size = USDC cost`. Inventory tracking, cash P&L, and MTM P&L all operate in share units — this is consistent.

Note: the paper Poisson fill model divides `volume_usd` (USD) by `size` (shares), creating a minor dimensional mismatch that inflates fill rates at low prices. Acceptable for plumbing validation; not trustworthy for strategy validation.

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
| `MAKER_EXCLUDED_CATEGORIES` | No | `""` | Comma-separated maker categories to skip (e.g. `weather`, `weather,sports`). Requires restart. |
| `MAKER_MAX_DAYS_TO_RESOLVE` | No | `7` | Skip markets resolving more than N days out |
| `MAKER_MIN_DAYS_TO_RESOLVE` | No | `0.17` | Skip markets resolving in less than ~4h |
| `MAKER_MIN_DAYS_TO_RESOLVE_WEATHER` | No | `0.33` | Weather-specific minimum (~8h) |
| `MAKER_MAX_INVENTORY_PER_MARKET` | No | `20` | Per-market share cap (non-weather) |
| `MAKER_MAX_TOTAL_INVENTORY` | No | `700` | Portfolio-wide share cap |
| `MAKER_MAX_DAILY_LOSS_PCT` | No | `0.03` | Stop all quoting if MTM loss exceeds this fraction of bankroll |
| `MAKER_WEATHER_INV_CAP` | No | `5` | Weather per-market share cap |
| `MAKER_SPORTS_INV_CAP` | No | `0` | Sports cap (0 = use MAKER_MAX_INVENTORY_PER_MARKET) |
| `MAKER_POLITICS_INV_CAP` | No | `0` | Politics cap (0 = use default) |
| `MAKER_CRYPTO_INV_CAP` | No | `0` | Crypto cap (0 = use default) |
| `MAKER_QUOTE_SIZE` | No | `10` | Base shares per ladder level |
| `MAKER_LADDER_LEVELS` | No | `3` | Bid/ask pairs per market |
| `MAKER_LEVEL_STEP` | No | `0.01` | Price offset between ladder levels |
| `MAKER_BASE_SPREAD` | No | `0.06` | Starting spread before adjustments |
| `MAKER_MIN_SPREAD` | No | `0.02` | Hard spread floor |
| `MAKER_MAX_SPREAD` | No | `0.15` | Hard spread ceiling |
| `MAKER_MAX_ACTIVE_MARKETS` | No | `30` | Max markets quoted simultaneously |
| `MAKER_MIN_DAILY_VOLUME` | No | `1000` | Min USD 24h volume to qualify |
| `MAKER_MIN_BID` | No | `0.10` | Skip near-zero markets |
| `MAKER_MAX_BID` | No | `0.90` | Skip near-certain markets |
| `MAKER_COOLDOWN_SECONDS` | No | `300` | Quote pause after inventory cap hit |
| `MAKER_ADVERSE_COOLDOWN_SECONDS` | No | `1800` | Extended pause for adverse selection / weather cap |
| `MAKER_RAPID_FILL_WINDOW` | No | `5.0` | Both sides filled within this window → pull quotes |
| `MAKER_ADVERSE_MIN_FILLS` | No | `5` | Min fills before directional check fires |
| `MAKER_ADVERSE_DIRECTION_PCT` | No | `0.80` | Fraction same-side to trigger adverse detection |
| `MAKER_PRE_RES_HOURS` | No | `2.0` | Force reduce-only within this many hours of expiry |
| `MAKER_SPREAD_MULT_WEATHER` | No | `2.0` | Category spread multiplier for weather |
| `MAKER_SPREAD_MULT_SPORTS` | No | `1.6` | Category spread multiplier for sports |
| `MAKER_SPREAD_MULT_CRYPTO` | No | `1.2` | Category spread multiplier for crypto |
| `MAKER_SPREAD_MULT_POLITICS` | No | `1.3` | Category spread multiplier for politics |
| `MAKER_SPREAD_MULT_FINANCE` | No | `1.0` | Category spread multiplier for finance |
| `MAKER_SPREAD_MULT_ENTERTAINMENT` | No | `2.2` | Category spread multiplier for entertainment |
| `MAKER_SPREAD_MULT_DEFAULT` | No | `1.5` | Fallback for uncategorised markets |
| `MAKER_VPIN_STALE_SECONDS` | No | `180` | Reset VPIN to neutral after this silence |
| `MAKER_NEGRISK_SIBLING_THRESHOLD` | No | `0.80` | Weather sibling mid above this → cancel losers |

### Maker Bot .env Cheat Sheet

Common operating modes — copy the relevant block into `.env` and restart:

```bash
# Conservative / cautious day
MAKER_QUOTE_SIZE=5
MAKER_MAX_ACTIVE_MARKETS=10
MAKER_MAX_DAILY_LOSS_PCT=0.01
MAKER_MAX_INVENTORY_PER_MARKET=10

# Pause weather markets entirely
MAKER_EXCLUDED_CATEGORIES=weather

# Scale up after live validation passes
MAKER_QUOTE_SIZE=20
MAKER_MAX_TOTAL_INVENTORY=1500
MAKER_MAX_INVENTORY_PER_MARKET=30

# Tighter spreads (more competitive quoting)
MAKER_BASE_SPREAD=0.04
MAKER_MIN_SPREAD=0.01
MAKER_SPREAD_MULT_SPORTS=1.2

# Widen a specific category (higher adverse selection observed)
MAKER_SPREAD_MULT_WEATHER=3.0

# More aggressive adverse-selection circuit breaker
MAKER_ADVERSE_MIN_FILLS=3
MAKER_ADVERSE_DIRECTION_PCT=0.70

# Earlier pre-resolution flatten (e.g. for same-day markets)
MAKER_PRE_RES_HOURS=4.0
```

## Assessing a Maker Bot Run

When asked to "assess the run" or "how did the bot do", execute this checklist in order:

### 1. Markout quality — `fills_markout.jsonl`
Parse all records, group by `interval_s` (5, 30, 60). Compute avg markout and adverse rate per interval.
Gate: `avg_markout_30s >= 0` and `adverse_rate_30s` stable.

### 2. Lifetime P&L — `maker_data/maker_lifetime.json`
Report `total_fills`, `total_cash_pnl`, `total_realized_pnl`, and `by_date` breakdown.
Flag any day with large negative `cash_pnl`.

### 3. Today's fills — `maker_data/maker_fills/maker_fills_YYYY-MM-DD.jsonl`
Count fills, sum `cash_flow` and `realized_pnl`. Check spread between buy/sell fill prices to verify edge capture.

### 4. Log scan — `logs/` directory
Check these five files for ERROR/WARNING lines:
- `maker.inventory.log` — cap hits, circuit breaker fires, orphaned positions
- `maker.order_manager.log` — order rejections, cancel storms
- `maker.quote_engine.log` — repricing errors, skew issues
- `maker.market_selector.log` — markets selected/dropped
- `maker.runner.log` — startup/shutdown, top-level errors

Summarize count and unique message types.

### 5. Pre-live gate status
Report pass/fail for each criterion:
1. Paper mechanics: quotes inside spread, inventory caps fire, CB stops exposure
2. Several hundred live fills collected
3. `avg_markout_30s >= 0`
4. `adverse_rate_30s` stable and acceptable
5. Net realized P&L after fees > 0 over multiple sessions
6. Inventory cap hits near zero; no uncontrolled drift

## Querying Resolved Markets

### How to look up resolution outcome for a token

Use the **CLOB API `/last-trade-price` endpoint** — not the Gamma API. The Gamma API's `clob_token_ids` parameter does not reliably match specific token IDs (returns unrelated markets or empty).

```
GET https://clob.polymarket.com/last-trade-price?token_id=<TOKEN_ID>
→ {"price": "0.001", "side": "BUY"}
```

Interpret the returned price:
- `price >= 0.95` → market resolved **YES** (token pays $1/share)
- `price <= 0.05` → market resolved **NO** (token pays $0/share)
- `0.05 < price < 0.95` → market still **open/unresolved**

### Resolution P&L calculation

For each token with a known net position from fills:

```python
# net_shares > 0 = net long YES; net_shares < 0 = net short YES (sold NO into market)
# net_cash = sum of cash_flows from fills (negative for net buys, positive for net sells)

if resolved_YES:
    payout = net_shares * 1.0
elif resolved_NO:
    payout = net_shares * 0.0   # longs get nothing; shorts keep their sale proceeds

pnl = net_cash + payout
```

### Script pattern (async, uses httpx)

```python
import asyncio, json, httpx
from collections import defaultdict

CLOB_URL = "https://clob.polymarket.com"

# Build net position per token from a fills JSONL file
net = defaultdict(lambda: {"shares": 0.0, "cost": 0.0, "question": ""})
with open("maker_data/maker_fills/maker_fills_YYYY-MM-DD.jsonl") as f:
    for line in f:
        r = json.loads(line)
        sign = 1 if r["side"] == "BUY" else -1
        net[r["token_id"]]["shares"] += sign * r["size"]
        net[r["token_id"]]["cost"] += r["cash_flow"]
        net[r["token_id"]]["question"] = r.get("question", "")

async def main():
    async with httpx.AsyncClient(timeout=15) as client:
        for tid, pos in net.items():
            r = await client.get(f"{CLOB_URL}/last-trade-price?token_id={tid}")
            await asyncio.sleep(0.08)   # polite rate limit
            price = float(r.json()["price"]) if r.status_code == 200 else None
            if price is None:
                continue
            if price >= 0.95:
                pnl = pos["cost"] + pos["shares"] * 1.0
            elif price <= 0.05:
                pnl = pos["cost"] + pos["shares"] * 0.0
            else:
                pnl = None   # still open
            print(f"{pos['question'][:50]}  shares={pos['shares']:+.2f}  pnl={pnl}")

asyncio.run(main())
```

### Known limitation: resolution P&L not tracked in checkpoint

`maker_checkpoint.json` zeroes inventory when markets resolve (paper mode runs `resolve_position()`) but **does not book the resolution cash** back into `realized_pnl`. The checkpoint's `realized_pnl` therefore reflects only spread capture from round-trip intra-session trades, not resolution payouts. To get the full economic picture, run the CLOB query above against the fills JSONL.

## Environment

Copy `.env.template` to `.env`. Only `POLY_PRIVATE_KEY` and `POLY_API_KEY` are needed for live mode. `FRED_API_KEY` adds consensus forecast detail (CPI/GDP/unemployment); basic rate/CPI data works without it via free CSV endpoints.
