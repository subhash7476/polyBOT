# Exit/Resolution Loop v1

## Summary
Add a resolution-driven position lifecycle that turns the bot from entry-only into entry -> resolution detection -> outcome recording -> realized P&L -> redemption/reconciliation.

Chosen defaults:
- Scope: resolution only for v1, no pre-resolution stop-loss/take-profit/signal-reversal exits.
- Source of truth: hybrid model, with local bot state as the trading-intent ledger and wallet/API data as reconciliation for resolution and redemption.
- Paper mode: mirror live behavior for outcome realization and P&L, but never perform on-chain redemption.

## Key Changes
- Introduce a persistent position record for bot-opened trades.
  - Expand the current in-memory `RiskManager` position model to include side, yes/no token used, entry size, entry price, opened timestamp, question, condition/market identifiers needed for resolution, and status (`open`, `resolved_pending_redeem`, `redeemed`, `closed_paper`).
  - Persist these records to a local JSONL or small state file so resolution handling survives process restarts.
- Add a dedicated `resolution_loop`.
  - Poll Polymarket position/market data on a fixed cadence.
  - For each bot-owned open position, detect whether the market is still active, closed-but-pending, or resolved.
  - On resolution, compute outcome from authoritative market/wallet data, mark the position resolved, update realized P&L, and remove it from active risk exposure.
  - In live mode, hand resolved wallet positions to the existing redeem flow; in paper mode, simulate the same state transition without redemption.
- Wire tracker and risk into realized outcomes.
  - Extend `CalibrationTracker.record_outcome()` usage so every bot-opened position gets its actual outcome recorded once resolution is known.
  - Add a realized-close path in `RiskManager` that closes positions based on outcome rather than only a manual exit price.
  - Daily P&L and consecutive-loss logic should update when a position resolves, not only on hypothetical manual close.
- Add wallet reconciliation.
  - Reconcile local open positions against Polymarket wallet positions to catch restart gaps, already-redeemed markets, and positions missing from local memory.
  - Restrict v1 to bot-owned positions only: wallet positions not opened by this bot are detected and logged as external/unmanaged, not imported into active strategy state.
- Surface lifecycle state in monitoring.
  - Dashboard/alerts should show counts for open, pending resolution, redeemable, and redeemed positions.
  - Emit alerts for resolution detected, outcome recorded, redemption success/failure, and reconciliation mismatches.

## Interface / Behavior Changes
- `RiskManager` gains an explicit resolution-close API, e.g. `resolve_position(token_id, resolved_yes)` or equivalent, which:
  - Computes payout from stored side/entry details.
  - Removes the position from open exposure.
  - Updates realized P&L and streak counters.
- Position persistence adds a bot-owned ledger file with stable fields needed for restart-safe lifecycle handling.
- `run_redeemall()` or a new wrapper should return enough structured result data to let the bot mark which tracked positions were successfully redeemed versus still pending.
- `main.py` orchestration adds a new concurrent loop for resolution handling and changes paper-mode behavior to stop relying on TTL expiry as the primary close mechanism.

## Test Plan
- Unit tests:
  - Resolve BUY_YES win/loss and BUY_NO win/loss with correct realized P&L.
  - Reconciliation behavior for: tracked position present in wallet, tracked position missing from wallet, external wallet position not owned by bot.
  - Paper-mode resolution path records outcome and closes exposure without redemption.
  - Live-mode resolved position transitions to pending redeem and then redeemed on successful redemption.
- Integration-style tests:
  - Bot opens a position, resolution loop sees market resolve, tracker outcome is written, risk exposure drops, dashboard state updates.
  - Restart scenario: persisted open position is reloaded and later resolved correctly.
  - Duplicate processing protection: the same resolved market is not closed or recorded twice.
- Failure scenarios:
  - Data API unavailable during polling.
  - Market closed but not yet resolved.
  - Redemption failure after successful resolution detection.
  - Wallet/API result disagrees with local state identifiers.

## Assumptions
- v1 does not implement discretionary exits before resolution.
- v1 only manages positions opened by this bot; manual/external wallet positions remain out of scope except for detection/logging.
- Outcome and redemption truth comes from Polymarket APIs/on-chain checks, not from local market snapshots.
- Current paper TTL expiry should be removed or demoted behind a feature flag once resolution-based paper closing is in place, because TTL-based "closing" corrupts realized-strategy evaluation.
