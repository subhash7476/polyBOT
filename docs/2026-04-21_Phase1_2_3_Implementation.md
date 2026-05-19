# Phase 1, 2 & 3 Implementation: Advanced Market Making Logic - 2026-04-21

## Overview
The `QuoteEngine` has been upgraded with three critical trading features: **External Price Discovery (Anchor Model)**, **Volatility-Adaptive Spreading**, and **Orderbook Imbalance (OBI) Micro-Skews**. These changes align the bot's quoting behavior with both macro (external exchange) and micro (Polymarket orderbook) signals.

---

## Phase 1: External Price Discovery (Anchor Model)
### Implementation Detail
*   **Theoretical Fair Value**: For crypto markets, a GBM-based probability is calculated using Binance spot prices and Deribit volatility.
*   **The Nudge**: If the external model deviates from the Polymarket mid, the `fair_value` is nudged by up to ±3¢. This allows the bot to "anticipate" moves before the Polymarket book fully adjusts.

---

## Phase 2: Volatility-Adaptive Spreading
### Implementation Detail
*   **Scaling Logic**: 
    *   **Baseline Volatility**: 60%.
    *   **Multiplier**: `dvol / 60.0`, clamped between `1.0` and `2.0`.
    *   **Result**: When Bitcoin volatility spikes (e.g., to 120%), the bot's base spread automatically doubles to protect against toxic flow.

---

## Phase 3: Orderbook Imbalance (OBI) Micro-Skew
### Implementation Detail
*   **Leading Indicator**: Uses `compute_obi_signal` from the `engine/` directory to detect persistent depth imbalances on the Polymarket CLOB.
*   **The Micro-Nudge**: 
    *   If the book is heavily weighted on the **BID** side (all bullish signals sustained over OBI_MIN_READINGS), the bot nudges its `fair_value` **UP** by up to 1¢.
    *   If the book is **ASK**-heavy, it nudges the `fair_value` **DOWN**.
*   **Purpose**: This captures short-term price pressure and "front-runs" the mid-price movement when the crowd is leaning one way.

---

## Technical Summary
- **Files Modified**: `maker/quote_engine.py`
- **Synergy**: The bot now anchors to macro exchanges (Phase 1), protects itself from volatility (Phase 2), and reacts to local orderbook pressure (Phase 3).
- **Risk Control**: All nudges are clamped (±3¢ for Anchor, ±1¢ for OBI) to ensure the bot remains competitive while leaning into signals.

## Next Steps
- **Phase 4: Bayesian Model Mapping** for non-crypto categories (Weather/Macro).
- **Phase 5: Aggressive Inventory Liquidation** triggered by markout tracking.
