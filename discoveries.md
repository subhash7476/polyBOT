# discoveries.md — Pattern & Backtest Log

## Format
Each entry: date | sample_size | win_rate | p_value | notes

## Signal Hypotheses (Pre-Backtest)

### Flatline Detector (2026-03-25)
- Hypothesis: Markets where YES price range < 2c over 48h before resolution resolve to leading side ~79-81%
- Parameters: window=48h, threshold=0.02, expiry_gate=72h, min_leading=0.60
- Weight: 0.20 (initial, uncalibrated)
- Target validation: min 50 markets, win_rate > 60%, p-value < 0.05
- Status: HYPOTHESIS — needs 50+ resolved markets in fills.jsonl to validate

### Order Book Imbalance (2026-03-25)
- Hypothesis: bid/ask depth ratio > 2.5 or < 0.4 sustained over 3 readings (15min apart) predicts direction
- Parameters: ratio_high=2.5, ratio_low=0.4, min_readings=3
- Weight: 0.10 (low — expected noise)
- Expected lag: short-term (hours), not predictive near resolution
- Status: HYPOTHESIS — noisy signal, validate first 50 trades

### Volume-Price Divergence (2026-03-25)
- Hypothesis: Volume spike (>2x rolling avg) without price movement (<2%) = accumulation
- Parameters: volume_multiple=2.0, price_move_max=0.02, lookback=24h
- Direction: inferred from price drift since spike (positive drift = bullish)
- Weight: 0.10 (low confidence — direction ambiguous without trade tape)
- Status: HYPOTHESIS — validate direction inference quality

### Complement Arb (2026-03-25)
- Status: DEFERRED
- Reason: In binary Polymarket markets, NO ask = 1 - YES bid by construction.
  YES ask + NO ask >= 1.0 always from mid prices. True complement arb requires
  independent NO token order book tracking (NO token best_ask from CLOB, not derived).
- Integration point: ContractState needs no_best_ask populated from actual NO token feed.
  Add to clob_monitor._handle_book() when NO token asset_id events arrive.

## Backtest Results
<!-- Populated as fills.jsonl accumulates resolved markets -->
