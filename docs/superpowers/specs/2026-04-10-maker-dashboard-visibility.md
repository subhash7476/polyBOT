# Maker Dashboard Visibility + Filter Fix

**Date:** 2026-04-10  
**Status:** Approved  
**Branch:** feat/v3-multi-asset-expansion

## Problem

The maker bot runs in shadow mode and produces data, but the operator cannot evaluate
what it is doing because:

1. **Shadow fill logs show token IDs, not market questions** — `[21514499]` is meaningless.
2. **Shadow fill proximity guard is too wide** — `_MAX_FILL_DISTANCE = 0.10` allows a trade
   at 0.001 to "fill" a bid at 0.090 (distance 0.089 < 0.10). This cannot happen on a real
   CLOB; the taker would hit the best bid, not walk 9¢ up the book.
3. **No runtime stale-market guard** — `MarketSelector` checks bid range every 15 minutes,
   but a market can collapse to near-zero between refreshes. `QuoteEngine` quotes whatever
   was selected, even if the price has moved out of range.
4. **Active Quotes table lacks context** — shows bot's own bid/ask but not: resolution date,
   real CLOB book depth, 24h volume, or category.
5. **No "Selected Markets" panel** — only markets with live orders appear in the dashboard.
   The operator cannot see which 30 markets the selector chose or why the bot is watching
   them but not quoting some.

## Goals

- Operator can read any log line and know which market it refers to.
- Operator can see all selected markets (up to 30) with resolution timing, volume, and depth.
- Operator can distinguish markets the bot is quoting from those it is only watching.
- Near-dead markets (bid < 0.05) cannot generate shadow fills or live quotes, regardless
  of when MarketSelector last ran.

## Non-Goals

- No changes to MarketSelector filter thresholds (they are correct; the issue is runtime staleness).
- No switch from shadow mode to paper mode (that is the next milestone after this work).
- No changes to Falcon, P&L, or feed-status dashboard sections.
- No full order book (level-2) display — best bid/ask + aggregate depth is sufficient.

---

## Changes

### ① Shadow fill proximity guard — `maker/shadow_fill_poller.py`

**Change:** Reduce `_MAX_FILL_DISTANCE` from `0.10` to `0.02`.

**Rationale:** On a real CLOB, a taker trade executes at the best available price. A trade at
0.001 hits the best bid (≈0.001), not a maker bid 9¢ higher. The guard exists to prevent
cross-book phantom fills; 2¢ is the correct maximum meaningful distance.

**Log line:** Add `cs.question[:50]` (looked up from `app_state.markets`) to the shadow fill
log message so every SHADOW FILL line names the market.

```
SHADOW FILL: BUY 10.00 @ 0.090 [Will BTC exceed $100k by Apr 15?] trade@0.088
```

**Defense-in-depth current-book check in `_check_fills`:** At the top of `_check_fills`,
look up `cs` from `self._app.markets` (already available via `self._app`). If
`cs is None or cs.best_bid < 0.05 or cs.best_bid > 0.95`, return `[]` immediately.

**Rationale:** There is a race window between QuoteEngine reprice cycles (up to 30s) during
which a market can collapse below 0.05. The per-token cancel from ② fires on the next
reprice cycle; until then, stale orders remain in `live_orders`. This check closes the race:
even if stale orders exist, fills are blocked as soon as the real book moves out of range.
`_check_fills` already receives a reference to the market state via `self._app`.

### ② Runtime bid-range guard — `maker/quote_engine.py`

**Change:** In `QuoteEngine._reprice`, immediately after fetching `cs`, if
`cs.best_bid < 0.05 or cs.best_bid > 0.95`:
1. Emit `CancelAll(token_id=token_id)` to `self._cancel_q` to actively pull resting orders.
2. Log a warning on first occurrence per token_id per session (suppress repeats).
3. Return early — do not compute or emit a new ladder.

**Rationale:** Skipping reprice alone leaves stale orders in `maker_state.live_orders`, which
`ShadowFillPoller` still reads when checking fills. The per-token cancel removes those orders
from live state immediately. `OrderManager.handle_cancel_sync` already handles per-token
cancel via `CancelAll(token_id=...)` at line 63 — no new infrastructure required.

**Implementation detail:** Track warned token_ids in `_stale_skip_warned: set[str]` on
`QuoteEngine`. Clear the set when a token exits `_active_token_ids` so a re-entry logs
again.

### ③ Enrich Active Quotes table — `maker/dashboard_loop.py` + `dashboard/static/maker.html`

**Backend (`dashboard_loop.py`):** Add the following fields to each dict in `active_markets`:

| Field | Source | Notes |
|---|---|---|
| `end_date_iso` | `cs.end_date_iso` | ISO string; passed to JS for formatting |
| `days_left` | computed from `end_date_iso` | float; negative = expired |
| `volume_24h` | `cs.volume_24h or cs.volume_usd` | USD |
| `book_bid` | `cs.best_bid` | real CLOB best bid, not bot's quote |
| `book_ask` | `cs.best_ask` | real CLOB best ask |
| `bid_depth` | `cs.bid_depth` | USDC notional depth across top-5 bid levels |
| `ask_depth` | `cs.ask_depth` | USDC notional depth across top-5 ask levels |
| `category` | `cs.category` | e.g. "election", "sports" |

**Frontend (`maker.html`):** Remodel the Active Quotes table with these columns:

| Column | Content |
|---|---|
| Market | question (60 chars), category badge below |
| Resolves | days_left formatted (e.g. "3.2d", "14h"), colored red if < 1 day |
| Vol 24h | volume_24h formatted as "$12.4k" |
| Book | `book_bid / book_ask` (real CLOB prices) |
| Depth | `bid_depth + ask_depth` total USDC notional (labeled "$") |
| Our Quote | bot's `bid / ask` |
| Spread | bot's spread |
| Inventory | existing bar widget |
| Status | QUOTED / COOLDOWN badge |

### ④ Selected Markets panel — `maker/state.py` + `maker/market_selector.py` + `maker/dashboard_loop.py` + `dashboard/static/maker.html`

**Problem:** The only way to know which markets are selected is to read logs. The dashboard
shows active quotes (markets with live orders) but not the full selected set.

**`MakerState`:** Add `selected_token_ids: set[str]` (protected by the existing `_lock`),
initialised to empty set in `__init__`.

**`MarketSelector.run()`:** After computing `selected`, acquire `maker_state._lock` and write
`maker_state.selected_token_ids = set(selected.keys())` directly, then put to
`active_markets_q` as before.

**Rationale for MarketSelector ownership (not QuoteEngine):** QuoteEngine drains
`active_markets_q` opportunistically during reprice cycles (up to 30s intervals). If
QuoteEngine wrote `selected_token_ids`, the dashboard could lag up to 60s behind the
selector's actual choice (30s queue drain + 2s dashboard tick). MarketSelector produces the
selection; writing it directly means the dashboard reflects the new set within 2s.
MarketSelector already holds `self._state: AppState`; passing `maker_state: MakerState` as
an additional constructor parameter is the minimal wiring change required.

**`dashboard_loop.py`:** Read `selected_token_ids` from `maker_state` snapshot. Build a
`selected_markets` list — one dict per token — from `markets_snapshot`. Fields:

| Field | Source |
|---|---|
| `token_id` | truncated to 16 chars |
| `question` | `cs.question[:70]` |
| `category` | `cs.category` |
| `book_bid` | `cs.best_bid` |
| `book_ask` | `cs.best_ask` |
| `spread` | `cs.best_ask - cs.best_bid` |
| `volume_24h` | `cs.volume_24h or cs.volume_usd` |
| `days_left` | computed from `cs.end_date_iso` |
| `is_quoting` | `token_id in live_orders` |

Push `selected_markets` to `dash.update(...)`. Add `selected_markets: list = []` to
`MakerDashboardState` and include it in `to_json()`.

**`maker.html`:** New panel "Selected Markets" (full-width, below Active Quotes) with columns:

| Column | Content |
|---|---|
| Status | `QUOTING` (green) / `WATCHING` (grey) badge |
| Market | question (70 chars) |
| Cat | category pill |
| Book | `book_bid / book_ask` |
| Spread | spread |
| Vol 24h | formatted USD |
| Resolves | days_left |

Sorted: QUOTING rows first, then by volume descending.

---

## Data Flow Summary

```
MarketSelector.run()
  → writes maker_state.selected_token_ids          (NEW — direct, 2s dashboard lag)
  → active_markets_q.put(set[token_ids])          (unchanged)

QuoteEngine.run()
  → reads active_markets_q                         (unchanged)
  → _reprice(): if best_bid out of range:
      emit CancelAll(token_id) to cancel_q         (NEW — pulls resting orders)
      skip ladder computation                      (NEW)

ShadowFillPoller._check_fills()
  → return [] if cs.best_bid out of range          (NEW — closes race window)
  → _MAX_FILL_DISTANCE = 0.02                      (CHANGED from 0.10)
  → log in run() includes cs.question             (NEW)

maker_dashboard_loop()
  → reads selected_token_ids from maker_state      (NEW)
  → builds selected_markets list                   (NEW)
  → enriches active_markets with depth/vol/dates   (NEW)
  → pushes both to MakerDashboardState             (NEW)

maker.html
  → Active Quotes table: 9 columns                 (CHANGED from 7)
  → Selected Markets panel: new full-width panel   (NEW)
```

---

## Testing

**Shadow fill proximity:**
- Trade at 0.001, bot bid at 0.090: no fill (distance 0.089 > 0.02).
- Trade at 0.088, bot bid at 0.090: fill generated (distance 0.002 ≤ 0.02).

**Shadow fill current-book check:**
- Market `best_bid = 0.04` (below range): `_check_fills` returns `[]` regardless of trade price.
- Market `best_bid = 0.06`: normal fill logic applies.

**QuoteEngine bid-range guard:**
- Market with `best_bid = 0.03`: CancelAll(token_id) emitted, reprice skipped, warning logged once per session.
- Market with `best_bid = 0.06`: quoted normally.
- After cancel, `live_orders[token_id]` is empty; subsequent shadow fill check for that market returns `[]`.

**Selected Markets panel:**
- MarketSelector writes `selected_token_ids` to MakerState at selection time.
- Dashboard shows updated panel within 2s of selection.
- Markets with live orders show QUOTING badge; others show WATCHING.

**Active Quotes table:**
- Shows `end_date_iso`-derived days_left, book_bid/ask, depth labeled as USDC ("$").
- Depth column does not use "shares" label.
