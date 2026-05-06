# Architecture Summary

This is a high-level system view for reviewers. It omits source code.

## 1. Discovery Layer
- fetches active Polymarket markets
- enriches with some event-based market sources
- keeps a subscribed in-memory market universe

## 2. Parsing Layer
- turns question text into structured contract metadata:
  - category
  - asset
  - threshold / directional interpretation
  - expiry
  - strategy lane

## 3. Probability / Signal Layer
- category-specific logic converts data inputs into a YES probability
- combines prior assumptions and live features

## 4. Filters
- signal sufficiency
- liquidity
- slippage proxy
- expected value threshold
- risk constraints

## 5. Execution
- currently mostly paper-mode entry simulation
- generally taker-style entry assumptions
- some strategy lanes have fast-exit logic

## 6. Lifecycle
- tracked position ledger
- resolution detection
- closed / resolved bookkeeping
- expired cleanup for stale short-dated markets

## 7. Tracking / Metrics
- fills log
- position ledger
- dashboard
- calibration and metrics reporting

## Main Current Weaknesses
- proven edge is missing
- some strategy lanes are still too long-dated for quick learning
- market quality is likely better than the bot’s models in several categories

## What Reviewers Should Focus On
- whether the strategy concept can work
- whether the chosen market classes are wrong
- whether the model assumptions are too weak for public markets
- whether execution style should change entirely
