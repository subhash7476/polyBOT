# Design Plan: Market Maker Pivot (Event-Driven CLOB)
**Date:** 2026-03-31
**Status:** APPROVED — Architectural Shift from Directional Taker to Market Maker

## Objective
Rebuild the bot's execution and strategy layers to profit from the bid-ask spread on Polymarket's Central Limit Order Book (CLOB), primarily targeting the "Short-Window Crypto" markets where the edge is most defensible.

## 1. Architectural Shift: Event-Driven Re-quoting
The current 5-second `trading_loop` is too slow for market making. Sophisticated participants will pick off stale quotes.
- **Old:** `main.py` scans all markets every 5s → Place market order if EV > threshold.
- **New:** `MMEngine` reacts to events → Every `book` or `price_change` message from `CLOBMonitor` triggers a re-calculation of Fair Value and a corresponding update to resting limit orders.

## 2. Core Components

### A. Quote Engine (`engine/mm_quote.py`)
- **Fair Value (FV) Calculation:**
  - `FV = (Binance Spot Signal * Weight) + (Polymarket Order Book Imbalance * Weight)`
  - Incorporates existing signals (DVOL, Vol Skew, Funding) but applies them to price prediction rather than a binary win/loss.
- **Spread Management:**
  - `Bid = FV - (BaseSpread / 2) - SkewCorrection`
  - `Ask = FV + (BaseSpread / 2) - SkewCorrection`
  - `BaseSpread` scales with market volatility (DVOL).

### B. Inventory Management (`trading/inventory.py`)
- **Inventory Skew:** If the bot is long 100 units, it should lower both its Bid and Ask to discourage further buys and encourage sells.
- **Position Limits:** Hard cap on USDC exposure per market and per category.
- **Delta Hedging (Phase 2):** (Optional) Hedge directional exposure on Deribit/Binance.

### C. Order Management System (OMS) (`trading/oms.py`)
- **User WebSocket:** Connect to Polymarket's private WS to receive `FILL` and `CANCEL` notifications.
- **Atomic Replace:** Use `cancel_all` + `create_order` or `replace_order` (if supported by CLOB) to minimize "unquoted" time.
- **Adverse Selection Filter:** Detect large incoming order flow or rapid price moves in the underlying and pull quotes instantly.

## 3. Implementation Phases

### Phase 1: Infrastructure (Days 1-7)
1.  **Private WebSocket Integration:** Implement `UserWSMonitor` using `py-clob-client` authentication to track fills in real-time.
2.  **OMS Development:** Create a wrapper for `CLOBExecutor` that manages *resting* orders rather than just *market* orders.
3.  **Basic Quote Engine:** Implement a constant-product or simple spread-around-mid model.

### Phase 2: Signal Integration (Days 8-14)
1.  **FV Signal Wiring:** Connect Binance Spot and Deribit DVOL feeds to the Quote Engine.
2.  **Inventory Skew Logic:** Implement the "skew" function to manage position risk.
3.  **Paper Mode MM:** Run the new system in paper mode to validate "Gross Maker Yield" (spread captured) vs "Adverse Selection Cost" (being picked off).

## 4. Key Files & Context

- `trading/executor.py`: Upgrade to handle limit orders and replacement.
- `market/clob_monitor.py`: Extend to pipe events directly to the `MMEngine`.
- `engine/mm_quote.py`: **NEW** — The heart of the market maker.
- `trading/inventory.py`: **NEW** — Tracks per-token exposure and calculates skew.

## 5. Verification & Testing
- **Latency Benchmarking:** Measure time from "Binance Price Update" to "Polymarket Order Replacement".
- **Simulation:** Backtest the spread-capture logic against historical `book` events from `fills.jsonl`.
- **Safety Check:** Ensure `Heartbeat Safety` pulls all orders if any feed (Binance or Polymarket) disconnects.

---
## Migration Strategy
The current `main.py` will be archived or modified to run the `MMEngine` as a separate task. We will maintain `PAPER=true` during the entire Phase 1.
