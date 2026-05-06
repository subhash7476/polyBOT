# 30-03-2026 Flaw Resolution Plan

## Purpose

This document captures the required fixes after reviewing the bot run and outcome audit through March 30, 2026.

The core conclusion is:
- the lifecycle bug prevented resolved weather positions from closing
- fixing that bug reveals a deeper issue: the weather trading logic itself appears materially wrong
- the bot is not ready for real-money deployment

## 1. Fix Lifecycle Truth First

### Problem

Resolved negRisk weather markets stayed open because the resolution loop only trusted `resolved=True`.

Observed API behavior:
- `closed=True`
- `resolved=None`
- decisive `outcomePrices=["1","0"]` or `["0","1"]`

Current effect:
- resolution loop heartbeats continue
- open positions never drain
- outcomes never reach `fills.jsonl`
- realized P&L remains zero

### Action

Patch [trading/resolution.py](/abs/path/D:/bot/polymarket-bot/trading/resolution.py) so that:
- `closed + decisive outcomePrices` counts as resolved
- both top-level `payload.closed` and nested `market.closed` are checked

Suggested logic:

```python
market = payload.get("market", {})
is_resolved = bool(payload.get("resolved") or market.get("resolved"))
resolved_yes = _resolved_yes_from_market_payload(payload) if is_resolved else None

is_closed = bool(payload.get("closed") or market.get("closed"))
if not is_resolved and is_closed:
    resolved_yes_candidate = _resolved_yes_from_market_payload(payload)
    if resolved_yes_candidate is not None:
        is_resolved = True
        resolved_yes = resolved_yes_candidate
```

### Tests

Add regression tests for:
- `resolved=True`
- `resolved=None, closed=True, outcomePrices=["1","0"]`
- `resolved=None, closed=True, outcomePrices=["0","1"]`

### Pass Criteria

- March 29 weather positions settle automatically on replay
- `fills.jsonl` gets `outcome` and `resolved_at`
- `positions.jsonl` open count drops correctly

## 2. Backfill and Settle Stuck Positions

### Problem

There are already resolved trades stuck in `open`.

### Action

Create a one-time settlement/backfill script that:
- scans tracked open positions
- queries Gamma/Polymarket for resolution state
- computes effective resolution from `closed + outcomePrices`
- writes:
  - `resolved_yes`
  - `resolved_at`
  - tracker outcome
  - realized P&L
- transitions those positions out of `open`

### Pass Criteria

- current zombie weather positions are drained
- dashboard, ledger, and API state reconcile

## 3. Disable Dangerous Weather Trading Immediately

### Problem

The current weather model appears systematically wrong for daily-high markets.

Observed result from the March 29 weather batch:
- `20` weather trades entered
- `19` effectively resolved
- `2` winners
- `17` losers
- estimated net P&L about `-$29.99`

This is not a lifecycle issue. It is a model-quality issue.

### Action

Immediately do one of:
- disable weather trade entry completely, or
- disable weather entry while still allowing scan/log visibility

This should stay in place until the weather model is corrected.

### Pass Criteria

- no new weather positions are opened under the current flawed logic

## 4. Rebuild the Weather Model Correctly

### Problem

The most likely flaw is that METAR current temperature is being used as if it predicts the end-of-day maximum too early in the day.

This is especially dangerous because:
- the bot opens many weather positions during local overnight or morning hours
- METAR at that time is closer to the overnight low than the daily high
- the model then becomes confidently wrong on exact and low-threshold daily maximum contracts

### Action

Redesign weather modeling for daily-high markets:

1. Use daily-high forecast as the primary source.
2. Treat METAR as optional and time-sensitive.
3. Add local-time awareness for each city.
4. Suggested rule:
   - if more than 4 hours remain before the local market resolution cutoff, ignore METAR for daily-high prediction
   - if within 4 hours, allow METAR only as a modest correction, not the dominant input
5. Review exact-temperature markets separately from threshold markets.

### Pass Criteria

- weather probabilities stop producing absurd near-certainty against market pricing
- paper validation shows materially improved calibration

## 5. Add Hard Sanity Guards on Probabilities and Sizing

### Problem

The bot can size into extreme-confidence trades produced by a fragile signal path.

### Action

Add category-specific safety guards:
- probability clipping for weather
- extra confirmation for exact-temperature markets
- smaller sizing when the contract price is extremely cheap and conviction comes from one signal path
- optional disagreement guard between model and market

Examples:
- reject or downsize if `abs(model_prob - market_prob)` is extreme but signal diversity is low
- cap size for exact buckets unless multiple model components agree

### Pass Criteria

- no more large bets driven by one fragile weather input
- tail-loss exposure is reduced

## 6. Add Category-Level Realized Metrics

### Problem

Aggregate stats are not enough. You need category truth.

### Action

Extend metrics reporting to show, by category and strategy:
- resolved count
- win rate
- realized P&L
- Brier score
- average entry edge

Minimum categories:
- weather
- rates
- crypto
- arb

### Pass Criteria

- weak categories are immediately visible
- weather cannot hide behind overall activity

## 7. Keep Rates and Crypto Only as Data-Collection Paths

### Problem

Rates and crypto do not yet have enough resolved outcomes to judge edge.

### Action

For now:
- keep them paper-only
- prefer faster-resolving crypto over long-dated macro for short-term validation
- do not infer profitability from open positions

### Pass Criteria

- resolved non-weather outcome count starts to grow

## 8. Add a Lifecycle Audit Command

### Problem

You need a single source of operational truth after every run.

### Action

Add a simple audit utility/report showing:
- truly live open positions
- resolved-but-still-open zombies
- pending redeem positions
- positions missing identifiers
- realized P&L since run start

### Pass Criteria

- one command reveals whether lifecycle health is good or broken

## 9. Re-Run Controlled Paper Validation

### Problem

You need proof that the fixes work in practice, not just in code.

### Action

After the lifecycle fix and weather disablement:
- run paper trading on fast-resolving non-weather markets
- confirm:
  - trades open
  - trades resolve
  - outcomes record
  - slots free naturally
  - realized P&L changes from zero

Do not restore weather trading until the redesigned weather model is in place.

### Pass Criteria

- at least one full open -> resolve -> close cycle completes automatically
- no zombie positions remain from that validation run

## 10. Real-Money Deployment Gate

Do not deploy real money until all of the following are true:

1. Resolution loop fix is deployed and verified.
2. No zombie positions remain after a fast-resolving paper run.
3. Weather trading is either disabled or revalidated after redesign.
4. `fills.jsonl` records actual resolved outcomes.
5. `positions.jsonl` drains naturally after resolution.
6. At least one non-weather category has enough resolved outcomes to judge quality.
7. Realized metrics are acceptable, not just open-position marks.

## Recommended Implementation Order

1. Patch resolution logic
2. Backfill and settle stuck positions
3. Disable weather entries immediately
4. Add lifecycle audit command
5. Redesign weather model
6. Add category-level realized metrics
7. Re-run controlled paper validation
8. Reassess deployment readiness

## Most Important Takeaway

The entry engine is working.

The lifecycle bug hid the fact that the weather model was losing money.

Fixing lifecycle is necessary, but not sufficient. The weather strategy must be treated as unsafe until redesigned and revalidated.
