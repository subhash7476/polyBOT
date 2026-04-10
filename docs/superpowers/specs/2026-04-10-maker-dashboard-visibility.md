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

### ② Runtime bid-range guard — `maker/quote_engine.py`

**Change:** In `QuoteEngine._reprice`, immediately after fetching `cs`, skip the market if
`cs.best_bid < 0.05 or cs.best_bid > 0.95`. Log a warning on the first skip per token_id
per session (suppress repeats to avoid log spam).

**Rationale:** MarketSelector refreshes every 15 minutes. A market can resolve or collapse
within that window. This guard is the last line of defence before a quote is computed and
sent. It is a pure safety check — no effect on healthy markets.

**Implementation detail:** Track skipped token_ids in a `_stale_skip_warned: set[str]`
instance variable on `QuoteEngine`. Log warning on first occurrence; subsequent skips are
silent until the market exits the selected set.

### ③ Enrich Active Quotes table — `maker/dashboard_loop.py` + `dashboard/static/maker.html`

**Backend (`dashboard_loop.py`):** Add the following fields to each dict in `active_markets`:

| Field | Source | Notes |
|---|---|---|
| `end_date_iso` | `cs.end_date_iso` | ISO string; passed to JS for formatting |
| `days_left` | computed from `end_date_iso` | float; negative = expired |
| `volume_24h` | `cs.volume_24h or cs.volume_usd` | USD |
| `book_bid` | `cs.best_bid` | real CLOB best bid, not bot's quote |
| `book_ask` | `cs.best_ask` | real CLOB best ask |
| `bid_depth` | `cs.bid_depth` | aggregate size of top-5 bid levels (shares) |
| `ask_depth` | `cs.ask_depth` | aggregate size of top-5 ask levels (shares) |
| `category` | `cs.category` | e.g. "election", "sports" |

**Frontend (`maker.html`):** Remodel the Active Quotes table with these columns:

| Column | Content |
|---|---|
| Market | question (60 chars), category badge below |
| Resolves | days_left formatted (e.g. "3.2d", "14h"), colored red if < 1 day |
| Vol 24h | volume_24h formatted as "$12.4k" |
| Book | `book_bid / book_ask` (real CLOB prices) |
| Depth | `bid_depth + ask_depth` total shares |
| Our Quote | bot's `bid / ask` |
| Spread | bot's spread |
| Inventory | existing bar widget |
| Status | QUOTED / COOLDOWN badge |

### ④ Selected Markets panel — `maker/state.py` + `maker/quote_engine.py` + `maker/dashboard_loop.py` + `dashboard/static/maker.html`

**Problem:** The only way to know which markets are selected is to read logs. The dashboard
shows active quotes (markets with live orders) but not the full selected set.

**`MakerState`:** Add `selected_token_ids: set[str] = field(default_factory=set)` (protected
by the existing `_lock`).

**`QuoteEngine.run()`:** When draining `active_markets_q`, also write the new set to
`maker_state.selected_token_ids` under `maker_state._lock`. No other actors need to write
this field.

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
  → active_markets_q.put(set[token_ids])          (unchanged)

QuoteEngine.run()
  → reads active_markets_q
  → writes maker_state.selected_token_ids          (NEW)
  → _reprice(): skip if best_bid out of range      (NEW)

ShadowFillPoller._check_fills()
  → _MAX_FILL_DISTANCE = 0.02                      (CHANGED from 0.10)
  → log includes cs.question                       (NEW)

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

- Shadow fill with trade at 0.001 and bot bid at 0.090: no fill generated (distance 0.089 > 0.02).
- Shadow fill with trade at 0.088 and bot bid at 0.090: fill generated (distance 0.002 ≤ 0.02).
- Market with `best_bid = 0.03` in QuoteEngine: skipped, warning logged once per session.
- Market with `best_bid = 0.06`: quoted normally (passes guard).
- Selected Markets panel: shows all N selected markets; markets with live orders show QUOTING badge.
- Active Quotes table: shows `end_date_iso`-derived days_left, book_bid/ask, depth.
