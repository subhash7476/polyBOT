# POLYMARKET BOT — Unblock the Funnel (Critical Fix Prompt)

## THE PROBLEM

The bot has been running 3 days. Out of ~3,000 markets discovered, only 4 tokens ever trade — all Fed rate cut markets expiring April 29. They cycle every 6 hours (paper TTL expires, re-enters same positions). Zero resolved outcomes. Zero learning. Zero edge discovery.

The funnel is clogged at every stage. Here's the diagnosis from reading the actual code and fills.jsonl:

## ROOT CAUSE ANALYSIS

### Bottleneck 1: `build_model_probability` requires Deribit DVOL — only 3 assets have it
```python
# engine/probability.py line ~75
if spot and asset_dvol and contract.target_price:
    lnorm_prob = lognormal_prob_above(...)  # only fires for BTC/ETH/SOL
else:
    prior = 0.5  # EVERY other asset gets this dead prior
```
Result: XRP, BNB, DOGE, ADA, AVAX all get `prior=0.5`. Then the only feed signal that might fire is `funding_rate` (1 signal). Signal filter needs ≥2 signals OR decisive prior (≥40% confidence). Prior confidence = `abs(0.5 - 0.5) × 2 = 0`. So these assets need 2+ signals but only get 1. **Every non-BTC/ETH/SOL crypto market dies at signal filter.**

### Bottleneck 2: Election/event markets have NO probability path
The parser correctly tags them `category="election"` or `category="event"`, but then `main.py` routes them to `build_model_probability` which is a CRYPTO engine. It needs `spot`, `asset_dvol`, `target_price` — election markets have none of these. Prior=0.5, zero feed signals fire, signal filter kills them. **Every election and event market dies at signal filter.**
### Bottleneck 3: Flatline signal CAN'T fire yet
Flatline requires:
- Market within 72h of expiry ✓ (some markets qualify)
- 5+ price observations spanning 90% of the 48h window ✗ (bot only started 3 days ago, and only records prices during trading loop scans — most markets don't accumulate enough history)
- Leading price > 0.60 ✓

Even if flatline DID fire, it would be the only microstructure signal (OBI/VPD aren't firing consistently either). One signal alone can't pass the filter unless the model prior is decisive — but for non-crypto markets the prior is 0.5. **Flatline is structurally unable to generate trades by itself.**

### Bottleneck 4: The ONLY path that works is "decisive prior + 1 signal"
Looking at fills.jsonl, every single trade uses this exception:
- Category: `rates`
- Prior: ~0.22 to 0.26 (from Poisson model) → confidence = ~0.48 to 0.56 → ABOVE 0.40 threshold
- Signal: `fed_cut_prob` (1 signal, weight=0.10)
- Filter: decisive prior (≥40%) + 1 confirming signal = PASS

This is the ONLY working path in the entire bot. Everything else is dead.

### Bottleneck 5: No short-dated markets being targeted
Polymarket has BTC/ETH/SOL 5-minute and 15-minute up/down markets. These resolve FAST — you'd get outcome data within hours, not months. But the contract parser doesn't recognize the question format ("Will BTC go up in the next 5 minutes?") because it looks for price targets ("Will BTC hit $100k").

## THE FIXES (in priority order)
### FIX 1: Realized vol fallback for non-DVOL assets (HIGHEST IMPACT)

In `engine/probability.py`, when `asset_dvol` is None for a crypto asset, compute realized volatility from Binance spot prices:

```python
# After checking for Deribit DVOL, add fallback:
if spot and not asset_dvol and contract.target_price:
    # Compute 30-day realized vol from Binance spot price history
    realized_vol = feeds.realized_vol.get(asset)  # annualized, same scale as DVOL
    if realized_vol:
        lnorm_prob = lognormal_prob_above(spot, contract.target_price, realized_vol / 100, T)
        prior = lnorm_prob if up else (1.0 - lnorm_prob)
        prior = float(np.clip(prior, 0.01, 0.99))
```

This means `feeds/microstructure.py` needs to:
1. Store a rolling window of spot prices per asset (it already polls every 60s)
2. Compute annualized realized vol from that window
3. Expose it as `feeds.realized_vol` dict

**Result:** XRP, BNB, DOGE, ADA, AVAX all get proper lognormal priors. Combined with `funding_rate` signal, they can pass the "decisive prior + 1 signal" filter. This should unlock dozens of crypto markets immediately.

### FIX 2: Short-dated market parser (FASTEST WINS)

Add parsing for Polymarket's fast-resolving crypto markets. These use formats like:
- "Will BTC go up in the next 5 minutes?"
- "Will ETH price increase in the next 15 minutes?"  
- "Bitcoin up or down next hour?"

In `engine/contract_parser.py`, add a new pattern block BEFORE the existing crypto parser:
```python
# Short-dated crypto direction markets: "Will BTC go up in the next 5/15 min?"
_SHORT_DATED_RE = re.compile(
    r'(?:will\s+)?(\w+)\s+(?:go\s+)?(up|down|increase|decrease).*?'
    r'(?:next|in)\s+(\d+)\s*(min(?:ute)?s?|hour|hours?)',
    re.IGNORECASE,
)
```

These markets don't have a `target_price` — they're directional (up/down from current spot). Set `direction = "above"/"below"` and `target_price = current_spot`. The lognormal model naturally handles "will price be above current price in T minutes" — it's just `P(S_T > S_0)` which depends on drift (≈0.5) modified by signals.

**Result:** Access to markets that resolve in minutes/hours. This is where you get outcome data fast and start calibrating the engine.

### FIX 3: Microstructure-only trading path for non-modeled markets

Add a new function in `engine/probability.py`:

```python
def build_microstructure_probability(
    contract: ParsedContract,
    contract_state: ContractState,  
    weights: dict,
) -> tuple[float, int, BayesianEngine]:
    """
    For markets without feed-based models (election, event, generic).
    Uses current market price as prior, adjusts only with microstructure signals.
    """
    # Use market mid as prior (the crowd's estimate)
    prior = contract_state.mid
    prior = float(np.clip(prior, 0.05, 0.95))
    engine = BayesianEngine(prior=prior)
    # Signals (flatline/OBI/VPD) are added in main.py AFTER this returns
    return engine.probability, engine.signal_count, engine
```

In `main.py`, route election/event/unknown categories to this function:
```python
# main.py trading_loop, inside the per-market loop:
if parsed.category in ("macro", "rates"):
    model_prob, signal_count, engine = build_macro_probability(parsed, feeds, SIGNAL_WEIGHTS)
elif parsed.category == "crypto":
    model_prob, signal_count, engine = build_model_probability(parsed, feeds, SIGNAL_WEIGHTS)
else:
    # election, event, unknown — market-price prior + microstructure signals only
    model_prob, signal_count, engine = build_microstructure_probability(parsed, contract_state, SIGNAL_WEIGHTS)
```

**But there's a catch:** with market_mid as prior, the microstructure signals need to push probability AWAY from market price to generate EV. Flatline does this (it says "leading side will win" with more confidence than the market implies). OBI and VPD also push in a direction. The EV gate then checks if the model disagrees with the market enough to trade.

### FIX 4: Relax signal filter for microstructure-only markets

The current filter requires ≥2 signals OR decisive prior + 1. For microstructure-only markets, the prior IS the market price (confidence ≈ 0), so the "decisive prior" exception never fires. We need:

In `engine/signal_filter.py`, add a third exception:

```python
# If ALL active signals are microstructure (flatline/OBI/VPD) AND at least 2 agree:
MICROSTRUCTURE_SIGNALS = {"flatline", "orderbook_imbalance", "volume_divergence"}
if all(s.name in MICROSTRUCTURE_SIGNALS for s in signals):
    if len(signals) >= 2:
        pass  # microstructure pair is sufficient
    elif len(signals) == 1 and signals[0].name == "flatline" and signals[0].confidence >= 0.7:
        pass  # high-confidence flatline alone is tradeable
    else:
        return False, f"only {len(signals)} microstructure signal(s) (need 2, or flatline with confidence ≥0.7)"
```

**Result:** Flatline + OBI, or Flatline + VPD, or high-confidence flatline alone can generate trades for election/event markets near resolution.
### FIX 5: More frequent market discovery + prioritize short-dated

`CLOBMonitor` fetches markets ONCE at startup. Short-dated markets appear and disappear constantly. Change:

1. Re-fetch from Gamma API every 10-15 minutes (not just at boot)
2. In `select_markets()`, boost sort priority for markets expiring within 24-72 hours — these are where flatline can actually fire AND where we get fast outcomes
3. `PARSEABLE_MARKET_RESERVE = 50` is too low. If fix 1-3 work, many more markets become parseable. Raise to 150.

In `clob_monitor.py`, change the run loop:

```python
async def _run(self):
    while True:
        async with httpx.AsyncClient() as client:
            token_map = await fetch_active_markets(client)
        token_map = select_markets(token_map)
        # ... seed state, subscribe WS ...
        # WS reconnect loop with 15-min re-discovery
        try:
            async with websockets.connect(...) as ws:
                await ws.send(json.dumps(subscribe_msg))
                # ... handle messages for 15 min, then break to re-discover
        except: ...
```

### FIX 6: Record price history for ALL subscribed markets, not just traded ones

Currently `record_flatline_price()` is only called inside the trading loop for markets that make it past `parse_contract`. But flatline needs 48h of history BEFORE it can fire. You need to record prices for ALL subscribed markets from the moment they're discovered.

Add a price recording step in the WS handler or in a separate background task:

```python
# In _handle_price or _handle_book in CLOBMonitor:
record_flatline_price(yes_token_id, new_mid)
record_volume(yes_token_id, contract_state)  
record_obi_reading(yes_token_id, contract_state)
```

This way, by the time a market enters the 72h-before-expiry window, it already has 48h of price history and flatline can actually fire.
### FIX 7: "Generic binary" category in contract parser

Most Polymarket markets are simple yes/no questions the parser currently skips ("Will X happen?", "Will Y announce Z?"). These are valid trading targets for microstructure-only signals.

Add a catch-all at the END of `parse_contract`:

```python
# After all specific category checks fail, if the question ends in "?" 
# and contains "will" or "yes" patterns, mark as generic binary:
if not contract.parseable and re.search(r'\bwill\b.*\?', q):
    contract.category = "event"  # route through microstructure path
    contract.direction = "yes"
    contract.expiry = _parse_expiry(question) or _gamma_end_date  # use Gamma API end_date
    contract.parseable = True
    log.debug(f"generic binary market: {question[:60]}")
```

Pass the Gamma API `endDate` through to the parser so it can use it as fallback expiry.

**Result:** Hundreds of currently-skipped markets become tradeable through the microstructure path. They won't trade immediately (need microstructure signals to fire), but they'll start accumulating price history for flatline detection.

---

## IMPLEMENTATION ORDER

Do these in sequence. Each fix unblocks more of the funnel:

```
FIX 1: Realized vol fallback              → unlocks 5 more crypto assets (dozens of markets)
FIX 2: Short-dated market parser           → unlocks fast-resolving markets (outcome data in hours)
FIX 5: Market re-discovery every 15 min    → catches short-lived markets
FIX 6: Record prices for all markets       → flatline can accumulate history before it's needed
FIX 3: Microstructure probability path     → election/event markets get a trading path
FIX 4: Relaxed signal filter               → microstructure signals can carry trades alone
FIX 7: Generic binary parser               → hundreds more markets enter the pipeline
```

After each fix: run `pytest`, confirm existing 401 tests pass, add tests for new code.
## EXPECTED IMPACT

| Metric | Before fixes | After fixes |
|--------|-------------|-------------|
| Parseable markets | ~50 of 3,000 | 500-1,000+ |
| Markets with active signals | 4 (all Fed rates) | 50-100+ |
| Markets generating trades | 4 (cycling same tokens) | 20-50+ across categories |
| Unique token IDs in fills.jsonl per day | 4 | 30+ |
| Time to first resolved outcome | April 29 (33 days away) | Hours (short-dated crypto) |
| Signal diversity | 1 signal per trade (`fed_cut_prob`) | 2-4 signals per trade |

## WHAT NOT TO CHANGE

- Risk management in `trading/risk.py` — keep all limits as-is
- Kelly sizing — keep 5% fraction, $50 cap
- EV threshold — keep 2% minimum (already lowered from 3%)
- Paper mode — stays ON
- Existing test suite — all 401 tests must keep passing

## LOGGING REQUIREMENT

After implementing fixes, add a diagnostic log line at the END of each trading_loop scan:

```python
log.info(
    f"FUNNEL: {n_total} discovered | {n_parseable} parsed | "
    f"{n_signal} signal_ok | {n_liquidity} liquid | {n_ev} ev+ | "
    f"{n_traded} traded | categories: {category_counts}"
)
```

Where `category_counts` is a dict like `{"crypto": 12, "rates": 4, "event": 8, "election": 2}` showing how many parseable markets exist per category. This tells us immediately whether the fixes are working.

## VALIDATION

After implementing all fixes and restarting the bot in paper mode:

1. Within 1 hour: `FUNNEL` log should show ≥100 parseable markets across ≥3 categories
2. Within 6 hours: fills.jsonl should have ≥10 unique token IDs (not just the 4 Fed markets)
3. Within 24 hours: at least 1 short-dated market should have resolved with `outcome != null`
4. Within 48 hours: flatline signal should have fired on at least 1 market near resolution
## CC INSTRUCTIONS

You are fixing a production bot that is stuck. This is surgery, not a rebuild. The architecture is sound — the funnel is just too narrow.

Read every file I've listed above before making changes. Understand the existing patterns. Match them.

Work autonomously. Implement Fix 1 through Fix 7 in the order listed. After each fix:
1. Run `pytest --tb=short -q` — all tests must pass
2. Add tests for the new code (match existing test density)
3. Commit with a clear message describing what the fix unlocks

After ALL fixes: restart `python main.py` in paper mode and monitor the `FUNNEL` log line. Report back with the first 3 scan outputs showing the market counts.

If a fix is harder than expected, skip it and move to the next. The fixes are independent — each one unblocks a different part of the funnel. Even implementing 3 out of 7 would be a massive improvement over the current 4-market loop.

**The goal is not perfection. The goal is OUTCOME DATA. We need markets that resolve so we can measure whether our signals actually predict anything. Every day we spend trading only 4 unresolvable Fed markets is a day wasted.**

Start now. Fix 1 first — realized vol fallback in microstructure.py + probability.py.