# Polymarket Bot — Post-Implementation Corrections
**Date:** 2026-03-18
**Session:** First paper run + live debugging
**Status:** All corrections applied and verified running

These are bugs and design gaps discovered during the first live paper run.
None were covered by the v2.1 patch plan. Document these before any future re-implementation.

---

## Correction 1: CLOB REST API — Wrong endpoint for market discovery

**File:** `market/clob_monitor.py`

**Problem:**
`GET https://clob.polymarket.com/markets` ignores all query params (`active=true`, `closed=false`).
It always returns markets sorted oldest-first — all 5,000+ are historical closed markets from 2023.
No `category` field exists on any market. Token outcomes use actual outcome names (e.g. `"Arizona State"`, `"Nevada"`), not `"YES"`/`"NO"`.

**Fix:**
Use the **Gamma API** (`https://gamma-api.polymarket.com/markets`) instead.
- Supports `active=true`, `closed=false`, `enableOrderBook=true`, `limit`, `offset` params
- Returns `clobTokenIds` as a JSON string `["yes_token_id", "no_token_id"]` — index 0 is YES, index 1 is NO
- Returns `bestBid`, `bestAsk`, `volumeClob` directly — no need to wait for WS book events
- No `category` field either — use `contract_parser.parse_contract()` as the gate

```python
# Correct subscription message format (user confirmed working):
subscribe_msg = {
    "assets_ids": token_ids,
    "type": "Market",
    "id": "1",
}
# NOT: {"type": "subscribe", "channel": "market", "markets": token_ids}
```

**Seed state immediately** from Gamma snapshot so trading loop has data before any WS event arrives.

---

## Correction 2: CLOBMonitor reconnect loop re-fetches Gamma API

**File:** `market/clob_monitor.py`

**Problem:**
`BaseFeed.start()` calls `_run()` in a loop on each reconnect. The original `_run()` called
`fetch_active_markets()` every time the WS dropped — expensive (~10s), caused duplicate state seeding,
and logged misleading "found N markets / seeded N markets" every 10 seconds.

**Fix:**
Move `fetch_active_markets()` and the state seed outside the reconnect loop. Use an inner
`while True` loop for WS reconnects only. Token map and state persist across reconnects.

```python
async def _run(self):
    # Fetch ONCE
    async with httpx.AsyncClient() as client:
        token_map = await fetch_active_markets(client)
    # Seed ONCE
    for yes_id, meta in token_map.items():
        ...seed state...
    # Reconnect loop — no re-fetch
    while True:
        try:
            async with websockets.connect(...) as ws:
                await ws.send(json.dumps(subscribe_msg))
                async for raw in ws:
                    ...
        except Exception as exc:
            self.log.warning(f"WS error: {exc} — reconnecting in 10s")
            await asyncio.sleep(10)
```

**Note on WS drops:** "no close frame received or sent" is normal — Polymarket server closes TCP
without a proper WS close frame. Harmless since state is seeded from Gamma REST. Does NOT
affect trading — the trading loop reads from `state.markets` which persists regardless of WS status.

---

## Correction 3: Risk manager allows re-entry on open positions

**File:** `trading/risk.py`

**Problem:**
`can_trade()` checked max_open_positions count and group exposure but never checked whether
the same `token_id` was already in `open_positions`. Result: the same 3 markets were
re-entered on every 5-second scan cycle, generating hundreds of fills.jsonl entries.

**Fix:**
Add as the **first check** in `can_trade()`:

```python
if token_id in self.open_positions:
    return False, "position already open"
```

Also: log this at `DEBUG` not `INFO` — it fires every 5 seconds per open position and is
extremely noisy at INFO level once the position cap is reached.

---

## Correction 4: BTC/ETH spot price never populated

**File:** `feeds/microstructure.py`

**Problem:**
`feeds.btc_price` and `feeds.eth_price` were always 0. The design assumed Deribit DVOL
messages carried `index_price` — they do not. Actual DVOL payload: `{timestamp, index_name, volatility}` only.
With `spot=0`, the `dvol_lognormal` signal was silently skipped every cycle.

**Fix:**
Pull `markPrice` from Binance FAPI `premiumIndex` endpoint (already polled for funding rate).
Add a second call for ETH:

```python
r1 = await client.get(f"{_BINANCE_URL}/fapi/v1/premiumIndex", params={"symbol": "BTCUSDT"})
d1 = r1.json()
fr = float(d1["lastFundingRate"])
btc_price = float(d1["markPrice"])       # ADD

r3 = await client.get(f"{_BINANCE_URL}/fapi/v1/premiumIndex", params={"symbol": "ETHUSDT"})
eth_price = float(r3.json()["markPrice"])  # ADD

await self._state.update_feeds(
    btc_funding_rate=fr,
    btc_open_interest=oi,
    btc_price=btc_price,   # ADD
    eth_price=eth_price,   # ADD
)
```

---

## Correction 5: dvol_lognormal as signal is too weak — use as prior

**File:** `engine/probability.py`

**Problem:**
Bayesian prior hardcoded to 0.5. The `dvol_lognormal` signal contributed at most
`weight × strength × confidence = 0.30 × -1.0 × 0.43 = -0.13` log-odds.
This moved the model from 50% to only 46.8% for a "Bitcoin $150k in 13 days" market
(true probability ≈ 0.001%). The model then bought YES because 46.8% >> 0.7% market price.

**Root cause:** The log-normal model is an *analytical estimate*, not a noisy signal.
It should anchor the model, not nudge it. Treating it as a signal with `confidence = min(1, T/30)`
made it too weak to overcome the flat 0.5 prior.

**Fix:**
Use `lognormal_prob_above()` as the Bayesian **prior**, not as a signal.
Other signals (skew, funding, macro) adjust from this analytical baseline.

```python
if spot and dvol and contract.target_price:
    lnorm_prob = lognormal_prob_above(spot, contract.target_price, dvol / 100, T)
    prior = lnorm_prob if up else (1.0 - lnorm_prob)
    prior = float(np.clip(prior, 0.01, 0.99))  # keep log-odds finite
else:
    prior = 0.5  # fallback until feeds warm up

engine = BayesianEngine(prior=prior)
# Then add vol_skew, funding_rate, macro_dxy as signals on top
```

**Result:** "Bitcoin $150k by March 31" now gets prior ≈ 0.01% (correct).
Market prices it at 0.7% → model < market → BUY_NO (correct direction).

---

## Correction 6: Deribit options chain has no delta field

**File:** `feeds/deribit.py`

**Problem:**
`_compute_skew()` found 25-delta options using `x["delta"]`. Deribit's
`markprice.options.btc_usd` channel sends only `{timestamp, iv, instrument_name, mark_price}`.
No `delta` field. Result: `call_25` / `put_25` always defaulted to first item, skew was
garbage or 0.0 depending on list order.

**Actual Deribit options payload (confirmed):**
```json
{"timestamp": 1773830232449, "iv": 1.3166, "instrument_name": "BTC-27MAR26-300000-C", "mark_price": 0.0}
```

**Fix:**
Use `mark_price` range `[0.005, 0.08]` BTC as a near-OTM proxy (deep OTM → mark≈0, deep ITM → mark>0.5).
Filter to options in that range, split by `-C`/`-P` suffix, compute median IV difference.

```python
# Near-OTM options: 0.005 ≤ mark_price ≤ 0.08 BTC
# Skew = median(put_iv) - median(call_iv) for those options
# Positive = downside bias (puts bid up)
```

**Remove:** `_compute_skew()`, `_compute_term_structure()`, `delta` field references.
Also remove `btc_term_ratio` state update (term structure was a placeholder returning 1.0).

---

## Correction 7: Contract parser — $1m parsed as 1.0

**File:** `engine/contract_parser.py`

**Problem:**
`PRICE_PATTERNS` handled `$85k` → 85,000 but not `$1m` → 1,000,000.
The regex `r"\$([0-9,]+(?:\.[0-9]+)?)[kK]?"` captured group(1)=`"1"` and group(0)=`"$1"`.
The `m` suffix was not in the regex and not in the multiplier logic.

Result: "Will bitcoin hit $1m before GTA VI?" → `target_price=1.0`.
`lognormal_prob_above(83000, 1.0, ...)` ≈ 1.0 → prior=0.99 → model=0.990 → BUY_YES at 99%.
Market prices it at 48.8% — completely spurious trade.

**Fix:**
```python
# Pattern — add [mM] to regex:
r"\$([0-9,]+(?:\.[0-9]+)?)[mMkK]?"

# Multiplier logic — check m before k:
suffix = match.group(0).lower()
if "m" in suffix:
    value *= 1_000_000
elif "k" in suffix:
    value *= 1_000
```

**Note:** `$2B` (billion) is not handled — but markets using that notation are market-cap questions
(MegaETH FDV), not BTC/ETH price contracts. They fail the asset check and are filtered out anyway.

---

## Operational Notes

### Scan visibility
Added scan summary log to `main.py` trading loop — fires every 5s:
```
scan: 35 markets | 35 parseable | 3 signal ok | 3 liquid | 3 ev+
```
Useful for diagnosing at which filter stage contracts are being dropped.

### Feed warm-up sequence
On startup, feeds become available in this order:
1. **t=0**: Gamma API seeds `state.markets` with bid/ask from snapshot
2. **t=0**: Deribit WS → DVOL available immediately on first tick
3. **t=0**: Deribit WS → vol skew available on first options batch (~924 instruments)
4. **t=60s**: Binance FAPI poll → `btc_price`, `eth_price`, `btc_funding_rate`
5. **t=300s**: Yahoo Finance poll → DXY, 10Y yield
6. **Inactive**: Glassnode (no API key set) — `btc_exchange_netflow` always None

Until step 4, `dvol_lognormal` prior falls back to 0.5. Any trades in the first 60s use
a flat prior — treat them as potentially noisy.

### Paper validation checklist (unchanged from v2.1)
Do NOT set `PAPER=false` until:
- 50+ signals in fills.jsonl, 20+ resolved outcomes
- Brier score < 0.20, mean edge > 3%
- No single day exceeds 5% bankroll loss

---

*Post-implementation corrections — 2026-03-18*
