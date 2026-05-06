# Broader Market Discovery

## Why this change exists

The bot used to subscribe only to markets that `engine/contract_parser.py` could parse.
In practice that meant roughly 20 to 22 markets out of ~3,000 active Polymarket CLOB markets.

That behavior looked like a "top 20" selector, but it was not.
It was an accidental side effect of this pipeline:

1. Fetch active markets from Gamma
2. Immediately discard any market the parser did not understand
3. Seed state only with the survivors
4. Subscribe only to those survivors on the CLOB websocket

This made market coverage artificially tiny and tightly coupled subscription breadth to model support.

## What changed

The CLOB monitor now separates:

- `subscription breadth`
- `trade eligibility`

### Before

- Discovery only kept parseable markets
- Websocket subscription was effectively parser-limited
- Dashboard and scan counts made the market universe look much smaller than it really was

### After

- Discovery keeps all active order-book markets with 2 outcome tokens
- Ranking uses Gamma metadata such as `volume24hr`, `liquidity`, and `endDate`
- A selector chooses which subset to subscribe to
- Trading still relies on `parse_contract()` during scan time, so unsupported markets are observed but not traded

This is the key design point:

> The bot can now watch many more markets than it can currently price.

## New config knobs

Added in `config.py`:

- `MARKET_CATEGORY_FILTER`
- `MARKET_SORT_MODE`
- `MAX_SUBSCRIBED_MARKETS`
- `PARSEABLE_MARKET_RESERVE`

### Defaults

- `MARKET_CATEGORY_FILTER=""`
  Empty means "all categories"
- `MARKET_SORT_MODE="hybrid"`
- `MAX_SUBSCRIBED_MARKETS=250`
- `PARSEABLE_MARKET_RESERVE=50`

### Supported sort modes

- `hybrid`
  Prioritizes recent activity and liquidity, then nearer expiry
- `volume_desc`
  Highest lifetime CLOB volume first
- `volume24h_desc`
  Highest recent volume first
- `liquidity_desc`
  Highest quoted liquidity first
- `expiry_asc`
  Soonest-expiring markets first

### Parseable reserve

`PARSEABLE_MARKET_RESERVE` reserves part of the subscription budget for markets the
current strategy can actually parse.

Example with defaults:

- `MAX_SUBSCRIBED_MARKETS=250`
- `PARSEABLE_MARKET_RESERVE=50`

The selector will:

1. take up to 50 parseable markets first
2. fill the remaining slots with the best-ranked markets from the broader universe

This keeps broad dashboard coverage without starving the trading loop of strategy-supported markets.

## Files changed

- `config.py`
  Added market selection config
- `market/clob_monitor.py`
  Discovery now keeps all active order-book markets, stores richer metadata, and applies ranked selection before websocket subscribe
- `tests/market/test_clob_selection.py`
  Added tests for selection behavior

## Important behavioral consequences

### 1. Broader subscription does not mean broader strategy support

The trading loop still calls `parse_contract()` and then dispatches into the existing pricing engines.
That means unsupported categories remain non-tradable unless the parser and model layer are expanded.

### 2. Dashboard totals are now more honest

`n_total` can now reflect the broader subscribed universe instead of just the parseable subset.

### 3. Top-volume and soon-to-expire filters are now first-class

Coders no longer need to hardcode parser-specific behavior just to change which markets are watched.

## Current limitation

The current strategy still only prices a narrow slice of Polymarket:

- crypto threshold markets
- some rates markets
- some macro markets

If the goal is to trade politics, entertainment, sports, elections, or generic event markets, the next step is not more discovery work.
The next step is adding:

- parsing support for those market types
- a valid probability model for each type
- risk controls appropriate to each type

## Recommended next steps

1. Decide the target tradable universes, not just the target subscribed universes
2. Expand `engine/contract_parser.py` only where a matching model exists or is planned
3. Keep market selection independent from trade modeling
4. Tune `MAX_SUBSCRIBED_MARKETS` based on websocket stability and dashboard usefulness
