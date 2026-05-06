# Polymarket Bot Codebase Updation Plan

## Purpose
This document merges the exit/resolution architecture plan with a practical build order for evolving the current bot from an entry-only trading engine into a measurable, restart-safe, data-rich, and eventually live-tradeable system.

The main principle is:
- lifecycle truth first
- data velocity second
- evidence-based pruning and tuning third
- live validation last

This is intentionally different from a purity-first plan that narrows the market universe too early. Right now the system's biggest weakness is not only that it cannot close and measure trades, but also that it does not yet generate enough resolved outcomes to calibrate itself. The roadmap below treats fast-resolving markets, especially weather, as a calibration accelerator rather than as scope creep.

## Current State
The bot already does the following:
- discovers markets
- parses contracts
- builds probabilities
- applies signal, liquidity, EV, and risk gates
- places entries
- exposes a dashboard and alerts

The main gaps are:
- no full exit/resolution lifecycle
- no restart-safe tracked position ledger
- no reliable realized P&L path
- no complete wallet reconciliation
- no outcome-complete calibration data
- no proven measurement loop for category quality

The current 6-hour paper TTL keeps the bot exploring, but it corrupts evaluation because positions are artificially expired rather than settled through actual resolution.

## Core Design Decisions
- Scope for lifecycle v1: resolution only
- No stop-loss, take-profit, or signal-reversal exits before resolution in v1
- Position truth model: hybrid
  - local tracked positions are the bot's trading-intent ledger
  - wallet/API data is authoritative for reconciliation, resolution, and redemption
- Paper mode must mirror live lifecycle behavior as closely as possible, except for on-chain redemption
- TTL-based paper expiry must not remain the primary closing path
- Universe should remain broad initially, but emphasis should shift toward fast-resolving categories
- Weather is an explicit early-priority category because it can produce daily resolved outcomes and accelerate calibration
- Category restriction should happen after category-level metrics exist, not before

## Strategic View
The bot should be developed in four layers:

### Layer 1: Truth Layer
Make every entry measurable end-to-end.

### Layer 2: Data Velocity Layer
Add or preserve fast-resolving categories so the truth layer receives enough resolved trades to matter.

### Layer 3: Evidence Layer
Measure which categories and strategy types actually work, then cut weak ones.

### Layer 4: Edge Expansion Layer
Add structural-arb execution and eventually run small live pilots only where paper evidence is positive.

## Ordered Build Plan

### Step 1: Add Persistent Position Ledger
Build a restart-safe source of truth for bot-opened positions.

What to change:
- Create a persistence module such as `trading/positions.py`
- Define a serializable tracked position record with:
  - `token_id`
  - `no_token_id`
  - `question`
  - `category`
  - `group_key`
  - `side`
  - `size_usdc`
  - `entry_price`
  - `market_price_at_open`
  - `opened_at`
  - `status`
  - `strategy_type`
  - `resolved_yes` nullable
  - `resolved_at` nullable
  - `redeemed_at` nullable
  - optional reconciliation metadata
- Persist transitions for:
  - open
  - resolved
  - redeemed
  - closed in paper mode
- Load open and pending positions at startup

Where:
- `trading/positions.py`
- `trading/risk.py`
- `main.py`

How it improves the bot:
- prevents losing tracked state on restart
- makes lifecycle state auditable
- creates a durable foundation for reconciliation and measurement

Pass criteria:
- open a paper position, restart the bot, and the position is restored
- repeated startup does not duplicate restored positions
- ledger entries exist for every lifecycle transition

### Step 2: Extend RiskManager Into A Lifecycle Manager
Make tracked positions resolution-aware instead of just open/close aware.

What to change:
- Extend `Position` metadata to include:
  - `side`
  - `status`
  - `strategy_type`
  - resolution fields
- Add APIs such as:
  - `open_position(...)`
  - `mark_resolved(token_id, resolved_yes, resolved_at)`
  - `mark_redeemed(token_id, redeemed_at)`
  - `close_paper_position(...)`
  - `active_positions()`
  - `resolved_pending_positions()`
- Realized payout logic:
  - `BUY_YES` payout is `1` if resolved yes, else `0`
  - `BUY_NO` payout is `1` if resolved no, else `0`
- Realized P&L:
  - `(payout - entry_price) * (size_usdc / entry_price)`
- Daily P&L and streak logic should update from actual lifecycle transitions

Where:
- `trading/risk.py`

How it improves the bot:
- enables correct settlement
- gives the bot real realized P&L
- allows dashboard and tracker to reflect true trade state

Pass criteria:
- tests cover BUY_YES win/loss and BUY_NO win/loss
- resolved positions leave active exposure immediately
- daily P&L and loss streak update correctly on settlement

### Step 3: Audit YES/NO Execution Realism Early
Validate entry-side math before relying on aggregate metrics.

What to change:
- Audit:
  - YES-side EV path
  - NO-side pricing transforms
  - fee treatment
  - slippage treatment
  - correct book-side usage for BUY_NO
- Confirm:
  - `1 - YES bid = NO ask`
  - `1 - YES ask = NO bid`
- Add explicit tests for NO-side EV and slippage

Where:
- `trading/ev_gate.py`
- `trading/slippage.py`
- tests under `tests/trading/`

How it improves the bot:
- prevents silent accounting or EV bugs on BUY_NO trades
- avoids false confidence from mixed aggregate metrics

Pass criteria:
- tests cover YES and NO EV/slippage paths
- sample BUY_NO trades use correct transformed price inputs
- no-side logic produces expected values in deterministic fixtures

### Step 4: Add Resolution Loop
Detect when tracked positions resolve and settle them automatically.

What to change:
- Add `resolution_loop()` to `main.py`
- Poll on a fixed cadence, e.g. 60-180 seconds
- For each tracked open position:
  - fetch market or wallet status from Polymarket
  - classify as:
    - active
    - closed but unresolved
    - resolved
- When resolved:
  - compute resolved outcome
  - update tracked position status
  - remove from active risk exposure
  - trigger tracker outcome recording
  - persist the transition
- Paper-mode behavior:
  - use the same Polymarket market-resolution polling as live mode
  - paper mode changes only settlement behavior, not resolution detection
  - when a paper-tracked market resolves, settle it locally without redemption

Where:
- `main.py`
- optional helper file `trading/resolution.py`

How it improves the bot:
- closes the largest architectural gap
- turns entries into measurable completed trades
- enables trustworthy performance evaluation

Pass criteria:
- mocked resolved market causes:
  - tracker outcome write
  - risk state transition
  - ledger update
  - dashboard open-count decrement
- unresolved-but-closed markets are not double-processed
- paper-tracked positions resolve from Polymarket market status and settle locally without any redemption attempt

### Step 5: Add Wallet Reconciliation
Use wallet/API truth to verify local tracked state and catch drift.

What to change:
- Add reconciliation helpers that:
  - fetch wallet positions
  - map wallet state to tracked positions
  - classify mismatches:
    - tracked locally, absent in wallet
    - present in wallet, absent locally
    - already redeemed
    - unmanaged external position
- For v1:
  - log unmanaged external positions
  - do not auto-import them into active bot state

Where:
- `trading/reconciliation.py`
- integrated from `resolution_loop()` or a separate low-frequency loop

How it improves the bot:
- prevents local state drift
- makes live operation safer
- catches restart and redemption mismatches

Pass criteria:
- mismatches are logged with clear labels
- tracked positions reconcile cleanly after restart
- unmanaged wallet positions are detected but not imported

### Step 6: Integrate Redemption With Tracked Lifecycle
Tie redemption into tracked resolved positions rather than running it blindly.

What to change:
- Refactor `run_redeemall()` or wrap it to return structured results
- Attempt redemption only for tracked positions in resolved-pending state
- After successful redemption:
  - mark tracked position redeemed
  - persist the transition
  - update dashboard state
- In paper mode:
  - skip chain redemption
  - still complete the local state transition

Where:
- `trading/redeemall.py`
- `trading/redeem.py`
- `main.py`

How it improves the bot:
- makes redemption lifecycle-complete
- separates resolved vs redeemed state
- removes ambiguity after resolution

Pass criteria:
- successful redemption updates tracked state to redeemed
- failed redemption leaves position in resolved-pending state
- paper mode never attempts on-chain redemption

### Step 7: Remove TTL As Primary Paper Exit Mechanism
Stop fake paper closures from corrupting evaluation.

What to change:
- Remove or disable `expire_paper_positions()` from the main lifecycle path
- Keep TTL only behind a debug or exploration flag if needed
- Paper positions must close only through resolution lifecycle handling
- Do not deploy this change until Step 4 is working end-to-end
- Before disabling TTL in active paper trading, ensure there is enough position capacity for unresolved markets
  - either raise `MAX_OPEN_POSITIONS` materially, e.g. into the 20-30 range for paper exploration
  - or rely on fast-resolving markets once resolution handling is already freeing slots naturally

Where:
- `main.py`
- `trading/risk.py`
- `config.py`

How it improves the bot:
- makes paper results structurally comparable to live results
- prevents fake slot freeing and fake completion
- avoids bricking the bot with five long-dated positions if resolution handling is not yet active

Pass criteria:
- paper positions do not disappear due to elapsed time alone
- only lifecycle resolution closes paper positions
- bot does not get stuck permanently at `MAX_OPEN_POSITIONS` after TTL removal

### Step 8: Make Calibration Tracker Outcome-Complete
Ensure every tracked opened trade eventually receives a real outcome.

What to change:
- Extend tracker records with stable join fields where needed
- Ensure `record_outcome()` is called exactly once per resolved tracked position
- Add fields such as:
  - `strategy_type`
  - `entry_side`
  - `resolved_at`
  - resolution source metadata

Where:
- `calibration/tracker.py`

How it improves the bot:
- gives the optimizer and metrics real resolution data
- makes historical analysis and weight tuning meaningful

Pass criteria:
- every tracked opened position ends with non-null outcome
- rerunning resolution logic does not duplicate outcome writes

### Step 9: Make Dashboard Reflect Lifecycle Truth
Convert the dashboard from a live scanner into an operator console.

What to change:
- Add lifecycle counts:
  - open
  - resolved pending redeem
  - redeemed
  - lifetime trades
  - lifetime wins
  - lifetime losses
  - realized P&L
- Keep unrealized mark-to-mid separate from realized P&L
- Ensure side-aware rendering and P&L are correct for both YES and NO
- Add lifecycle panels and counters

Where:
- `dashboard/state.py`
- `dashboard/loops.py`
- `dashboard/static/index.html`

How it improves the bot:
- shows whether trades actually progress through completion
- exposes stuck positions and lifecycle bottlenecks

Pass criteria:
- dashboard clearly distinguishes open, resolved-pending, and redeemed positions
- realized P&L is separate from unrealized P&L
- lifecycle counts reconcile with the ledger

### Step 10: Add Weather As A Calibration Accelerator
Introduce fast-resolving, forecast-driven markets to accelerate learning.

What to change:
- Implement the planned weather market integration
- Route weather markets into:
  - discovery
  - parsing
  - probability model path
  - tracker
  - lifecycle loop
- Add weather category tagging in tracker and metrics
- Prioritize daily-resolving weather markets where model inputs are measurable
- Use `docs/weather-integration-plan-v2.md` specifically as the implementation reference, not the earlier weather plan
- Preserve the v2 corrections around:
  - unit normalization
  - target date extraction
  - forecast source selection, including HRRR vs GFS handling
  - sigma persistence and calibration behavior

Where:
- follow the existing weather planning docs under `docs/`
- likely changes across `engine/`, `market/`, `feeds/`, and `main.py`

How it improves the bot:
- generates many more resolved outcomes per week
- creates a calibration flywheel:
  - more resolved trades
  - better metrics
  - better weights and thresholds
- provides a measurable category rather than pure novelty breadth

Pass criteria:
- weather markets appear in discovery and tracking
- at least one weather trade can be opened, resolved, and recorded end-to-end
- category metrics can isolate weather from all other categories
- implementation follows `docs/weather-integration-plan-v2.md` rather than the earlier superseded weather plan

### Step 11: Preserve Broad Coverage, But Prioritize Fast-Resolving Markets
Do not narrow the universe yet. Bias it toward high-resolution categories instead.

What to change:
- Keep broad category support for now
- Adjust selection and ranking to favor:
  - weather
  - ultra-short-dated crypto markets, especially 5-minute and 15-minute BTC/ETH up/down markets
  - short-dated crypto thresholds
  - short-dated event/election binaries
  - other high-resolution or high-turnover markets
- Do not hard-prune categories before metrics exist

Where:
- `market/clob_monitor.py`
- `market/market_screener.py`
- `config.py`

How it improves the bot:
- increases resolved-outcome throughput
- avoids starving the calibration system
- preserves optionality until evidence exists
- uses the existing parser and funnel unlock work to generate outcome data much faster than long-dated macro markets

Pass criteria:
- discovered and evaluated universe remains broad
- ranking shows a measurable bias toward faster-resolving markets
- resolved outcomes per week increase materially
- short-dated crypto markets are explicitly present in the prioritized set and contribute resolved outcomes

### Step 12: Keep Existing Funnel Gates, But Improve Observability Instead Of Duplicating Them
The bot already has a funnel. The work now is to measure it better, not clone it.

What to change:
- Do not add a redundant hard eligibility gate layer unless data shows a real need
- Improve observability for the existing gates:
  - parseability
  - signal filter
  - slippage/liquidity
  - EV
  - risk
- Make skip reasons more structured and category-aware

Where:
- `main.py`
- `dashboard/`
- optionally `market/contract_filter.py`

How it improves the bot:
- avoids duplicating existing gating logic
- makes the funnel easier to reason about
- preserves the fixes already made to unblock trade flow

Pass criteria:
- funnel reasons are grouped clearly in logs or dashboard
- no duplicate pre-filter logic is introduced without a demonstrated need

### Step 13: Split Strategy Types In Tracking
Keep directional and structural edge separate throughout the system.

What to change:
- Add `strategy_type` to:
  - tracked positions
  - tracker logs
  - dashboard summaries
  - metrics
- Use values such as:
  - `directional`
  - `arb_monotonicity`
  - `arb_cross_temporal`
  - future fast-resolution strategy tags as needed

Where:
- `trading/risk.py`
- `calibration/tracker.py`
- dashboard files

How it improves the bot:
- avoids mixing incomparable strategies
- makes metrics and future risk controls more meaningful

Pass criteria:
- every trade record includes a non-null strategy type
- dashboard and metrics can summarize by strategy type

### Step 14: Add Category-Level Metrics
Measure which categories actually work before cutting any of them.

What to change:
- Extend metrics output to group by:
  - category
  - strategy type
  - side
  - holding duration
- Report:
  - trade count
  - realized P&L
  - Brier score
  - mean entry edge
  - win rate
  - average cost drag

Where:
- `calibration/metrics.py`

How it improves the bot:
- lets data decide what to keep and what to kill
- prevents premature narrowing
- exposes whether weather or any other category is actually useful

Pass criteria:
- one report can show performance by category and strategy type
- best and worst slices are obvious from output
- category comparisons are based on resolved outcomes, not open marks

### Step 15: Prune Categories Based On Evidence
Only now should the universe be narrowed.

What to change:
- Use category metrics to decide:
  - which categories to keep
  - which to de-prioritize
  - which to remove entirely
- Promote categories with:
  - enough sample size
  - better realized P&L
  - better calibration
  - acceptable cost drag

Where:
- `config.py`
- `market/clob_monitor.py`
- `market/market_screener.py`

How it improves the bot:
- turns category pruning into an evidence-based decision
- avoids starving the system before it has enough data

Pass criteria:
- any category removal is justified by category metrics
- retained categories have better measured performance than removed ones

### Step 16: Turn Arb Scanner Into Executable Bundles
Upgrade structural-arb detection into real trading logic.

What to change:
- Extend arb output to include:
  - legs
  - expected net profit
  - fee-adjusted return
  - recommended sizing
- Require net-of-fees and net-of-slippage profitability
- Add execution rules:
  - reject incomplete bundles unless safe
  - cap bundle capital
  - track all legs under arb strategy type

Where:
- `engine/arb_scanner.py`
- `main.py`
- `trading/executor.py`
- `trading/risk.py`

How it improves the bot:
- structural mispricing is more likely to be a real edge than broad forecasting
- gives the bot a second, cleaner strategy class

Pass criteria:
- mocked violations produce executable arb bundles
- unprofitable bundles are rejected after cost adjustment
- executed arb bundles are logged and tracked as arb trades

### Step 17: Add Separate Risk Controls For Arb And Directional
Do not let different strategy classes compete under one risk bucket.

What to change:
- Add config for:
  - `MAX_DIRECTIONAL_EXPOSURE_PCT`
  - `MAX_ARB_EXPOSURE_PCT`
  - `MAX_ARB_BUNDLE_SIZE_USDC`
- Separate exposure tracking and enforcement for arb vs directional

Where:
- `config.py`
- `trading/risk.py`

How it improves the bot:
- makes exposure control strategy-aware
- prevents one strategy from silently disabling the other

Pass criteria:
- directional and arb limits are enforced independently
- exceeding one does not incorrectly block the other

### Step 18: Tune Thresholds And Weights Only After Lifecycle Truth Exists
Optimize only on resolved, trustworthy data.

What to change:
- Revisit:
  - `MIN_EV_THRESHOLD`
  - slippage cutoffs
  - signal count thresholds
  - category-specific filters
- Use optimizer and resolved historical outcomes to remove weak signals or lower-value features

Where:
- `config.py`
- `calibration/weight_optimizer.py`

How it improves the bot:
- moves tuning from intuition to evidence
- helps suppress weak or noisy signal paths

Pass criteria:
- threshold changes are backed by resolved-trade data
- post-change metrics improve on at least one key measure without obvious deterioration elsewhere

### Step 19: Run Small Live Pilot
Use real money only after the system is lifecycle-complete and measured.

What to change:
- Limit live mode initially to a narrow slice with positive paper evidence
- Candidate slices:
  - weather if metrics support it
  - rates directional if metrics support it
  - structural arb if execution proves stable
- Reduce max trade size and exposure further for pilot mode
- Require clean reconciliation and lifecycle state before each session

Where:
- `main.py`
- `config.py`

How it improves the bot:
- validates execution quality without exposing full capital to unfinished architecture

Pass criteria:
- live pilot can run without orphaned positions
- wallet reconciliation remains clean
- resolved live outcomes flow into the same metrics system as paper trades

## Exit/Resolution Architecture Summary
This section governs the lifecycle work in Steps 1 through 9.

### Lifecycle Summary
Add a resolution-driven position lifecycle that turns the bot from entry-only into:
- entry
- resolution detection
- outcome recording
- realized P&L
- redemption
- reconciliation

### Key Lifecycle Requirements
- tracked positions must survive restart
- resolution must come from authoritative market or wallet data
- realized P&L must use final outcome, not mark-to-mid
- paper mode must simulate lifecycle truth
- wallet reconciliation must detect unmanaged or mismatched positions
- dashboard and alerts must expose lifecycle state

### Required Interface Changes
- `RiskManager` must expose a resolution-aware close path
- position persistence must support restart-safe loading and state updates
- redemption must return structured results that can update tracked lifecycle state
- `main.py` must run a concurrent resolution loop

### Lifecycle Test Requirements
- BUY_YES and BUY_NO resolution P&L cases
- paper-mode resolution settlement
- live-mode resolved-to-redeemed path
- restart restore and later resolution
- duplicate-processing protection
- reconciliation mismatch handling
- API unavailable and pending-not-resolved failure modes

## Milestones

### Milestone A: Lifecycle Complete
Includes Steps 1 through 9.

Success definition:
- the bot can open, persist, resolve, record, and close or redeem a trade end-to-end
- dashboard and ledger agree on lifecycle state
- YES and NO side accounting is audited and correct

### Milestone B: Data Velocity Established
Includes Steps 10 through 12.

Success definition:
- fast-resolving markets, especially weather, are feeding daily or near-daily resolved outcomes
- ultra-short-dated crypto markets are producing minute-scale resolved outcomes
- the funnel is observable and not artificially narrowed
- resolved trade volume is high enough to support real metrics

### Milestone C: Evidence Layer Complete
Includes Steps 13 through 15.

Success definition:
- categories and strategy types can be compared on resolved outcomes
- category pruning decisions are evidence-based

### Milestone D: Edge Expansion
Includes Steps 16 through 18.

Success definition:
- arb execution exists
- strategy-aware risk controls exist
- tuning is based on trustworthy data

### Milestone E: Live Validation
Includes Step 19.

Success definition:
- small live trading behaves operationally correctly and flows into the same measurement pipeline

## What Not To Prioritize Yet
- advanced ML
- cosmetic dashboard work beyond operational visibility
- discretionary pre-resolution exits
- high-frequency behavior
- broad threshold tuning before enough resolved outcomes exist

These can all wait until lifecycle truth and outcome volume exist.

## Immediate Recommended Sequence
If implementation starts now, the first concrete sequence should be:
1. `trading/positions.py`
2. extend `RiskManager` lifecycle methods
3. audit YES/NO execution math
4. add `resolution_loop()` in `main.py`
5. wire tracker outcomes
6. integrate redemption status transitions
7. remove TTL as primary paper close
8. add dashboard lifecycle counters
9. add weather integration as the first calibration accelerator
10. improve category and funnel observability
11. only later prune categories based on metrics
12. then build executable arb

## Final Standard For Progress
This codebase should not be judged by number of entries placed.

It should be judged by whether it can:
- enter trades
- carry them across restart
- settle them correctly
- record outcomes automatically
- separate realized from unrealized performance
- generate enough resolved outcomes to calibrate itself
- show which categories and strategy types actually work
- survive a small live pilot without state drift or orphaned positions
