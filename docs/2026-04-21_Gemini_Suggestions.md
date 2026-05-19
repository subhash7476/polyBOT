# Gemini CLI Strategic Recommendations - 2026-04-21

## 1. Current Architectural Assessment
The bot utilizes a sophisticated actor-based model for market making (`maker/`) and a secondary taker orchestrator (`main.py`). The core strength lies in its multi-feed integration (Falcon, Deribit, Binance), but there is a disconnect between "Signal Generation" and "Execution Pricing" where many advanced feeds are not yet influencing the `QuoteEngine`.

## 2. Profitability & Trading Sense Improvements

### A. External Price Discovery (Crypto Markets)
*   **The Issue:** Polymarket prices often lag behind major exchanges (Binance, Coinbase). Relying on the Polymarket `mid` makes the bot vulnerable to "latency arbitrage" by faster takers.
*   **The Fix:** Use the `MicrostructureFeed` (Binance Spot) to derive a real-time theoretical fair value. For binary options like "BTC > $70k", use Black-Scholes or a simple CDF of the spot price vs. strike, adjusted by `Deribit` volatility.

### B. Volatility-Adaptive Spreading
*   **The Issue:** `BASE_SPREAD` is currently static (0.06). In high-volatility regimes, this spread is too narrow, leading to rapid inventory exhaustion and adverse selection.
*   **The Fix:** Scale the `spread` parameter in `QuoteEngine` by the `DVOL` index. High DVOL = wider spreads; low DVOL = tighter, more competitive spreads.

### C. Delta Hedging
*   **The Issue:** The bot carries significant directional risk. Inventory skewing is the only tool used to manage this, which is "passive" and slow.
*   **The Fix:** Implement an automated hedging module that opens offsetting positions on Binance Perps or Deribit when inventory in a highly correlated market (e.g., Crypto) exceeds a certain threshold.

### D. Orderbook Imbalance (OBI) as a Leading Indicator
*   **The Issue:** The bot reacts to price changes after they happen on the Polymarket CLOB.
*   **The Fix:** Incorporate the OBI signal from `engine/orderbook_imbalance.py` into the `QuoteEngine`. If the book is 80% weighted on the BID side, nudge the fair value up *before* the mid price shifts.

### E. Activating Bayesian Priors
*   **The Issue:** Specialized engines for Weather and Rates are currently underutilized in the `maker` logic.
*   **The Fix:** Map the outputs of `engine/weather_probability.py` and `engine/macro_probability.py` directly to the `model_adj` parameter in the `QuoteEngine` to provide a non-market-derived "anchor" for fair value.

### F. Aggressive Inventory Liquidation
*   **The Issue:** `reduce_only` mode currently just stops one side of quoting.
*   **The Fix:** If `markout_tracker` detects consistent adverse selection (losing money 30s after fills), the bot should trigger an "Aggressive Exit" where it crosses the spread to liquidate inventory rather than waiting for a counterparty to come to it.

## 3. Recommended Implementation Roadmap
1.  **Phase 1:** Wire Binance/Deribit spot and vol into `QuoteEngine` for crypto-category fair value.
2.  **Phase 2:** Implement DVOL-based dynamic spreading.
3.  **Phase 3:** Integrate OBI micro-skews.
4.  **Phase 4:** Bayesian model mapping for Weather/Macro.
