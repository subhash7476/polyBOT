# Plan: Polymarket Bot Real-Time Dashboard

## Context
The bot is running (24 markets, multiple signals) but there's no visibility into what's happening.
The user needs a single-page dashboard to see: live feed data (spot prices, vol, macro), active markets being evaluated, scan funnel stats, and bot status — all updating in real time.

## Architecture

```
asyncio event loop (main.py)
  └─ dashboard_loop()          ← reads AppState every 2s, writes DashboardState (threading.Lock)

Flask daemon thread            ← reads DashboardState (threading.Lock), never touches asyncio
  ├─ GET /                     ← serves index.html
  └─ GET /stream               ← SSE: pushes JSON blob every 2s
```

Browser: single EventSource connection to `/stream`, updates DOM in-place.

**Key constraint**: asyncio locks in AppState are loop-bound — only the `dashboard_loop` coroutine (inside the event loop) reads AppState. Flask thread reads only from `DashboardState` (plain threading.Lock).

## Files to Create

### `dashboard/__init__.py`
Empty package marker.

### `dashboard/state.py`
`DashboardState` singleton:
- `threading.Lock` for cross-thread safety
- Fields: `bot_status`, `uptime_seconds`, `spot_prices` (dict), `dvol` (dict), `funding_rates` (dict), `vol_skew` (dict), `dxy`, `dxy_confidence`, `yield_10y`, `fed_may_cut_prob`, `fed_expected_cuts`, `sofr`, `cpi`, `unrate`, `positions` (list), `active_markets` (deque, maxlen=50), `scan_stats` (dict)
- **Per-source timestamps**: `feed_updated_at: dict[str, float]` — keyed by feed name (`"binance"`, `"deribit"`, `"macro"`, `"onchain"`); updated in `dashboard_loop` whenever values change
- `update(snapshot: dict)` — acquires lock, updates fields
- `to_json()` — acquires lock, returns JSON-serializable dict (converts deque to list, timestamps to "Xs ago" strings)

**Position data structure** (each item in `positions` list):
```python
{
  "token_id": str,
  "question": str,
  "side": "BUY_YES" | "BUY_NO",
  "size_usdc": float,
  "entry_price": float,
  "current_mid": float,
  "pnl_usdc": float,
  "opened_at": str,   # ISO timestamp
}
```

### `dashboard/loops.py`
`async def dashboard_loop(state: AppState, dash: DashboardState)`:
- Runs every 2 seconds inside the asyncio event loop
- Acquires `state._lock` (async with), reads FeedState + positions
- Calls `dash.update(snapshot)`

Also: `update_scan_stats(dash, **kwargs)` — called from `trading_loop` after each scan cycle to push `n_total`, `n_parseable`, `n_signal`, `n_liquidity`, `n_ev`, `n_traded`.

### `dashboard/server.py`
Flask app:
- `GET /` → serve `dashboard/static/index.html`
- `GET /stream` → SSE response; calls `dash.to_json()` every 2s, yields `data: {json}\n\n`
- `start_dashboard_server(dash, port=5050)` → starts Flask in daemon thread

### `dashboard/static/index.html`
Single-page HTML with inline CSS + vanilla JS (no build step):

**Layout (6 sections)**:
1. **Status bar** (top): bot running/stopped, uptime, paper/live mode indicator; per-feed staleness badges (e.g. "Binance 47s ago", "FRED 4m ago") — colored green/amber/red by age
2. **Spot prices + vol grid**: table rows per asset (BTC/ETH/SOL/XRP/BNB/DOGE/ADA/AVAX) — price, DVOL, funding rate, vol skew; cells flash green/red on change; column header shows "last updated Xs ago" per source (Binance vs Deribit update at different intervals)
3. **Macro panel**: DXY + confidence, 10Y yield, SOFR, Fed cut prob, expected cuts, CPI YoY, unemployment; each value shows "last updated Xs ago" badge
4. **Open positions**: table of current paper positions (market, direction, size, entry price, P&L)
5. **Active markets table**: last N markets evaluated — question, model prob, market mid, EV, signal count, pass/fail reason
6. **Scan funnel**: Total → Parseable → Signal filter → Liquidity → EV gate → Traded (numbers + bar chart)

EventSource reconnects automatically on disconnect.

## Files to Modify

### `main.py`
1. Import `DashboardState`, `dashboard_loop`, `start_dashboard_server`, `update_scan_stats`
2. After AppState init: `dash = DashboardState(); start_dashboard_server(dash)`
3. Add `dashboard_loop(state, dash)` to `asyncio.gather()` call
4. Update `trading_loop` signature: `async def trading_loop(state, risk, executor, tracker, dash: DashboardState)`
5. In `trading_loop`: after the existing scan stats `log.info()`, call `update_scan_stats(dash, n_total=..., n_parseable=..., n_signal=..., n_liquidity=..., n_ev=..., n_traded=...)`
6. Update the gather() call to pass `dash` into `trading_loop`

### `requirements.txt`
Add `flask>=3.0`

## Critical Files (read before implementing)
- `main.py` — understand gather() call and trading_loop signature
- `market/state.py` — FeedState fields, AppState._lock usage
- `trading/executor.py` — position data structure for open positions display

## Verification
1. `pip install flask`
2. Start bot: `python main.py`
3. Open browser: `http://localhost:5050`
4. Verify: spot prices update within 60s (Binance poll), macro updates within 5 min, scan funnel counts increment each cycle
5. Kill bot — verify browser shows "disconnected" indicator (EventSource fires `onerror`)
