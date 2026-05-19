# Phase 1 & 2 Implementation: Advanced Market Making Logic - 2026-04-21

## Overview
The `QuoteEngine` has been upgraded with two critical trading features: **External Price Discovery (Anchor Model)** and **Volatility-Adaptive Spreading**. These changes align the bot's quoting behavior with real-time global crypto market conditions.

---

## Phase 1: External Price Discovery (Anchor Model)
### Implementation Detail
*   **Contract Parsing**: The repricing loop now identifies markets using `parse_contract`.
*   **Theoretical Fair Value**: For crypto markets, a GBM-based probability is calculated using Binance spot prices and Deribit volatility.
*   **The Nudge**: If the external model deviates from the Polymarket mid, the `fair_value` is nudged by up to ±3¢. This allows the bot to "anticipate" moves before the Polymarket book fully adjusts.

---

## Phase 2: Volatility-Adaptive Spreading
### Implementation Detail
*   **DVOL Integration**: `compute_spread` now accepts a `dvol` parameter (annualized volatility).
*   **Scaling Logic**: 
    *   **Baseline Volatility**: 60%.
    *   **Multiplier**: `dvol / 60.0`, clamped between `1.0` and `2.0`.
    *   **Result**: When Bitcoin volatility spikes (e.g., to 120%), the bot's base spread automatically doubles. When volatility is at or below baseline, the bot maintains its competitive `BASE_SPREAD`.
*   **Dynamic Update**: Spreads are recalculated on every price tick or force-reprice, ensuring immediate protection during flash crashes or volatility spikes.

---

## Technical Summary
- **Files Modified**: `maker/quote_engine.py`
- **Impact**: 
    - **Risk Mitigation**: wider spreads during high vol prevent "stop-loss hunting" and toxic fills.
    - **Alpha Generation**: Anchor model captures edge from lagging prices on Polymarket.
    - **Stability**: Prevents inventory exhaustion during one-sided volatility events.

## Next Steps
- **Phase 3: Orderbook Imbalance (OBI) Integration** to further refine micro-skews.
- **Phase 4: Bayesian Model Mapping** for non-crypto categories (Weather/Macro).
