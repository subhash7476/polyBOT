# Advanced Market Making Logic Implementation (Phases 1-5) - 2026-04-21

## Overview
The `QuoteEngine` has been fully upgraded with a 5-phase strategic enhancement plan. The bot now combines global macro signals, local micro-structure, and real-time performance feedback to optimize market-making profitability and risk management.

---

## Phase 1 & 4: Multi-Category Bayesian Anchoring
*   **Crypto Markets**: Anchored to Binance Spot and Deribit Volatility.
*   **Weather Markets**: Anchored to ECMWF/HRRR meteorological forecasts.
*   **Macro/Rates**: Anchored to consensus forecasts and Fed probability models.
*   **Logic**: fair value is nudged toward the theoretical model (clamped at ±3¢), allowing the bot to lean into expected price moves.

---

## Phase 2: Volatility-Adaptive Spreading
*   **Logic**: Spreads are dynamically scaled by the asset's real-time volatility (DVOL).
*   **Impact**: Automatically widens quotes during high-volatility regimes to prevent adverse selection and reduces them during stable periods to remain competitive.

---

## Phase 3: Orderbook Imbalance (OBI) Micro-Skew
*   **Logic**: Detects persistent depth imbalances on the Polymarket CLOB.
*   **Impact**: Nudges fair value by up to ±1¢ to capture short-term price pressure before the mid-price actually shifts.

---

## Phase 5: Aggressive Inventory Liquidation
*   **The Problem**: Sometimes inventory becomes "toxic" because the bot's model was wrong or the market is moving too fast.
*   **The Solution**: 
    *   **Markout Integration**: `MarkoutTracker` now exports rolling 30s average markouts per market to `MakerState`.
    *   **Aggressive Exit**: If a market is in `reduce_only` mode AND its 30s markout drops below **-10 bps**, the bot triggers an `aggressive_exit`.
    *   **Execution**: Instead of quoting at the mid, the bot **crosses the spread by 2¢** on the closing side to ensure immediate liquidation of the toxic position.

---

## Technical Summary
- **Primary Logic**: `maker/quote_engine.py`
- **State Management**: `maker/state.py` (added `rolling_markouts`)
- **Performance Tracking**: `maker/markout_tracker.py` (integrated with `MakerState`)
- **Orchestration**: `maker/runner.py` (updated actor wiring)

## Final Result
The bot now possesses "Trading Sense"—the ability to use external data to anchor its prices, protect itself during volatility, and aggressively cut losses when it detects it is being "picked off" by informed traders.
