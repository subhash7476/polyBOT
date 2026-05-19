# Phase 1 Implementation: External Price Discovery (Anchor Model) - 2026-04-21

## Overview
Phase 1 has been successfully integrated into the `QuoteEngine`. The bot now utilizes external real-time data from Binance (Spot) and Deribit (DVOL/Skew) to anchor its fair value for crypto-indexed markets on Polymarket.

## Key Changes
1.  **Contract Parsing Integration**: `QuoteEngine` now uses `engine.contract_parser.parse_contract` to identify and categorize markets in real-time during the repricing loop.
2.  **Model-Based Probability Calculation**: For crypto markets, the bot invokes `engine.probability.build_model_probability`, which uses a lognormal prior (GBM) driven by Binance spot prices and Deribit volatility.
3.  **Model Anchor Nudging**: 
    *   The bot compares the **Model Probability** (external) against the **Polymarket Mid** (internal).
    *   A `model_anchor_adj` is calculated as the delta between the two, clamped at ±0.03 (3 cents).
    *   This adjustment is applied to the base `fair_value`, allowing the bot to "lean" into price moves before the Polymarket order book has fully adjusted.
4.  **Signal Synergy**: The model probability incorporates not just spot price, but also volatility skew, funding rates, and other crypto-specific signals, providing a high-confidence anchor.

## Technical Details
- **File Modified**: `maker/quote_engine.py`
- **New Dependencies**: `engine.contract_parser`, `engine.probability`, `config.SIGNAL_WEIGHTS`.
- **Logic Placement**: The anchor logic is calculated inside the `_reprice` loop for each token, ensuring that every quote update is informed by the latest external signals.

## Expected Impact
- **Toxic Flow Reduction**: By anchoring to Binance/Deribit, the bot is less likely to be "picked off" by arbitrageurs when the broader crypto market moves.
- **Improved Edge Capture**: The bot will quote slightly more aggressively (leaning) when its external models suggest the Polymarket price is lagging.
- **Competitiveness**: Tightened spreads via book-relative quoting are now "safer" because the center of the spread is anchored to a more liquid discovery venue.

## Next Steps
- Monitor `model_anchor` logs to verify the frequency and magnitude of adjustments.
- Proceed to **Phase 2: Volatility-Adaptive Spreading** to dynamically adjust the spread width based on the same DVOL feeds.
