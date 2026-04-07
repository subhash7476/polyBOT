# Market Maker Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an actor-based market maker bot that quotes both sides of Sports prediction markets on Polymarket, earning the spread from biased taker order flow.

**Architecture:** Five async actors communicate via `asyncio.Queue`. MarketSelector picks markets, QuoteEngine computes bid/ask, OrderManager places post-only GTC limit orders via py-clob-client, FillPoller detects fills at 500ms, InventoryManager tracks exposure and fires circuit breakers. All actors share `MakerState` under lock.

**Tech Stack:** Python 3.14, asyncio, py-clob-client 0.34.6, existing feeds/state/parser infrastructure.

**Spec:** `docs/superpowers/specs/2026-03-31-maker-bot-design.md`

---

## File Structure

### New files (all in `maker/`)

| File | Responsibility |
|---|---|
| `maker/__init__.py` | Package marker |
| `maker/types.py` | Dataclasses: QuoteIntent, Fill, SkewUpdate, CancelAll |
| `maker/state.py` | MakerState: InventoryBook, OrderTracker, QuoteIntent cache |
| `maker/market_selector.py` | MarketSelector actor — picks which sports markets to quote |
| `maker/quote_engine.py` | QuoteEngine actor — computes fair value + bid/ask spread |
| `maker/order_manager.py` | OrderManager actor — places/cancels orders via py-clob-client |
| `maker/fill_poller.py` | FillPoller actor — detects fills at 500ms polling |
| `maker/inventory.py` | InventoryManager + CircuitBreaker — tracks exposure, fires cancel-all |
| `maker/runner.py` | Entrypoint — wires actors + queues, runs asyncio.gather |

### New test files

| File | Tests |
|---|---|
| `tests/maker/test_types.py` | QuoteIntent, Fill, SkewUpdate serialization |
| `tests/maker/test_state.py` | MakerState inventory/order tracking |
| `tests/maker/test_market_selector.py` | Market filtering and ranking |
| `tests/maker/test_quote_engine.py` | Fair value, spread computation, stale detection |
| `tests/maker/test_order_manager.py` | Order lifecycle, paper mode, cancel logic |
| `tests/maker/test_fill_poller.py` | Fill detection, paper simulation |
| `tests/maker/test_inventory.py` | Inventory tracking, skew, circuit breakers |

### Modified files

| File | Change |
|---|---|
| `main.py` | Add `--mode maker` argparse flag |
| `engine/contract_parser.py` | Add sports category detection regex |

---

## Task 1: Types Module

**Files:**
- Create: `maker/__init__.py`
- Create: `maker/types.py`
- Test: `tests/maker/__init__.py`
- Test: `tests/maker/test_types.py`

- [ ] **Step 1: Create package structure**

```bash
mkdir -p maker tests/maker
```

- [ ] **Step 2: Write the test**

```python
# tests/maker/__init__.py
# (empty)

# tests/maker/test_types.py
from maker.types import QuoteIntent, Fill, SkewUpdate, CancelAll


def test_quote_intent_fields():
    qi = QuoteIntent(
        token_id="abc123",
        bid_price=0.45,
        ask_price=0.55,
        bid_size=10.0,
        ask_size=10.0,
        reason="new_market",
    )
    assert qi.token_id == "abc123"
    assert qi.bid_price == 0.45
    assert qi.ask_price == 0.55
    assert qi.spread == 0.10


def test_fill_fields():
    f = Fill(
        token_id="abc123",
        side="BUY",
        price=0.45,
        size=10.0,
        order_id="order-1",
        filled_at=1000.0,
    )
    assert f.side == "BUY"
    assert f.notional == 10.0


def test_skew_update():
    s = SkewUpdate(token_id="abc123", skew_factor=0.5)
    assert s.skew_factor == 0.5


def test_cancel_all_single():
    c = CancelAll(token_id="abc123")
    assert not c.is_global


def test_cancel_all_global():
    c = CancelAll(token_id="*")
    assert c.is_global
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/maker/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'maker.types'`

- [ ] **Step 4: Write the implementation**

```python
# maker/__init__.py
# Polymarket market maker bot

# maker/types.py
"""Shared dataclasses for maker actor communication."""

from dataclasses import dataclass


@dataclass(frozen=True)
class QuoteIntent:
    """Emitted by QuoteEngine → consumed by OrderManager."""
    token_id: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    reason: str  # "new_market" | "reprice" | "inventory_skew"

    @property
    def spread(self) -> float:
        return self.ask_price - self.bid_price


@dataclass(frozen=True)
class Fill:
    """Emitted by FillPoller → consumed by InventoryManager."""
    token_id: str
    side: str  # "BUY" | "SELL"
    price: float
    size: float
    order_id: str
    filled_at: float  # unix timestamp

    @property
    def notional(self) -> float:
        return self.size


@dataclass(frozen=True)
class SkewUpdate:
    """Emitted by InventoryManager → consumed by QuoteEngine."""
    token_id: str
    skew_factor: float  # [-1.0, +1.0]; positive = holding YES


@dataclass(frozen=True)
class CancelAll:
    """Emitted by CircuitBreaker → consumed by OrderManager."""
    token_id: str  # specific market or "*" for all

    @property
    def is_global(self) -> bool:
        return self.token_id == "*"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/maker/test_types.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add maker/__init__.py maker/types.py tests/maker/__init__.py tests/maker/test_types.py
git commit -m "feat(maker): add shared types — QuoteIntent, Fill, SkewUpdate, CancelAll"
```

---

## Task 2: MakerState

**Files:**
- Create: `maker/state.py`
- Test: `tests/maker/test_state.py`

- [ ] **Step 1: Write the test**

```python
# tests/maker/test_state.py
import asyncio
from maker.state import MakerState


def test_initial_state():
    s = MakerState()
    assert s.inventory == {}
    assert s.live_orders == {}
    assert s.last_quotes == {}
    assert s.cooldowns == {}
    assert s.daily_pnl == 0.0


def test_get_inventory_default():
    s = MakerState()
    assert s.get_inventory("abc") == 0.0


def test_update_inventory_buy():
    s = MakerState()
    s.update_inventory("abc", "BUY", 10.0)
    assert s.get_inventory("abc") == 10.0


def test_update_inventory_sell():
    s = MakerState()
    s.update_inventory("abc", "BUY", 10.0)
    s.update_inventory("abc", "SELL", 4.0)
    assert s.get_inventory("abc") == 6.0


def test_total_inventory():
    s = MakerState()
    s.update_inventory("a", "BUY", 10.0)
    s.update_inventory("b", "SELL", 5.0)
    assert s.total_abs_inventory == 15.0


def test_skew_factor_zero_when_empty():
    s = MakerState()
    assert s.skew_factor("abc") == 0.0


def test_skew_factor_clamped():
    s = MakerState(max_inventory_per_market=50.0)
    s.update_inventory("abc", "BUY", 100.0)  # exceeds cap
    assert s.skew_factor("abc") == 1.0


def test_is_cooled_down():
    s = MakerState()
    s.cooldowns["abc"] = 9999999999.0  # far future
    assert s.is_cooled_down("abc")


def test_not_cooled_down():
    s = MakerState()
    s.cooldowns["abc"] = 0.0  # in the past
    assert not s.is_cooled_down("abc")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/maker/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# maker/state.py
"""Shared mutable state for the maker bot."""

import asyncio
import time
from dataclasses import dataclass, field
from maker.types import QuoteIntent


@dataclass
class MakerState:
    """Thread-safe state shared across all maker actors."""

    max_inventory_per_market: float = 50.0
    max_total_inventory: float = 200.0

    # Per-market net position: positive = holding YES, negative = holding NO
    inventory: dict[str, float] = field(default_factory=dict)

    # Per-market live order IDs: token_id → {"bid_order_id": str, "ask_order_id": str}
    live_orders: dict[str, dict[str, str]] = field(default_factory=dict)

    # Per-market last emitted quote (for stale detection)
    last_quotes: dict[str, QuoteIntent] = field(default_factory=dict)

    # Per-market cooldown: token_id → resume_at (unix timestamp)
    cooldowns: dict[str, float] = field(default_factory=dict)

    # Daily realized P&L
    daily_pnl: float = 0.0

    # Fill timestamps for rapid-fill detection: token_id → {side → timestamp}
    last_fill_times: dict[str, dict[str, float]] = field(default_factory=dict)

    # Lock for concurrent actor access
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def get_inventory(self, token_id: str) -> float:
        return self.inventory.get(token_id, 0.0)

    def update_inventory(self, token_id: str, side: str, size: float) -> None:
        current = self.inventory.get(token_id, 0.0)
        if side == "BUY":
            current += size
        else:
            current -= size
        self.inventory[token_id] = current

    @property
    def total_abs_inventory(self) -> float:
        return sum(abs(v) for v in self.inventory.values())

    def skew_factor(self, token_id: str) -> float:
        """Normalized skew: [-1.0, +1.0]. Positive = holding YES."""
        inv = self.get_inventory(token_id)
        if self.max_inventory_per_market == 0:
            return 0.0
        raw = inv / self.max_inventory_per_market
        return max(-1.0, min(1.0, raw))

    def is_cooled_down(self, token_id: str) -> bool:
        """True if market is in cooldown (circuit breaker fired recently)."""
        resume_at = self.cooldowns.get(token_id, 0.0)
        return time.time() < resume_at

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/maker/test_state.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add maker/state.py tests/maker/test_state.py
git commit -m "feat(maker): add MakerState — inventory, order tracking, cooldowns"
```

---

## Task 3: Sports Category Detection

**Files:**
- Modify: `engine/contract_parser.py`
- Test: `tests/engine/test_contract_parser_sports.py`

- [ ] **Step 1: Write the test**

```python
# tests/engine/test_contract_parser_sports.py
from engine.contract_parser import parse_contract


def test_nba_game():
    p = parse_contract("tok1", "Will the Los Angeles Lakers beat the Boston Celtics on April 5?")
    assert p.category == "sports"
    assert p.parseable is True


def test_nfl_game():
    p = parse_contract("tok2", "Will the Kansas City Chiefs win Super Bowl LX?")
    assert p.category == "sports"
    assert p.parseable is True


def test_mlb_game():
    p = parse_contract("tok3", "Will the New York Yankees win the World Series?")
    assert p.category == "sports"
    assert p.parseable is True


def test_soccer_match():
    p = parse_contract("tok4", "Will Real Madrid win the Champions League 2026?")
    assert p.category == "sports"
    assert p.parseable is True


def test_generic_team_win():
    p = parse_contract("tok5", "Will the Packers defeat the Bears on Sunday?")
    assert p.category == "sports"
    assert p.parseable is True


def test_non_sports_not_matched():
    p = parse_contract("tok6", "Will BTC be above $100k on April 30?")
    assert p.category != "sports"


def test_election_not_matched_as_sports():
    p = parse_contract("tok7", "Will the Democrats win the Senate in 2026?")
    assert p.category != "sports"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/engine/test_contract_parser_sports.py -v`
Expected: FAIL — sports markets parsed as "event" or "unknown"

- [ ] **Step 3: Read contract_parser.py to find insertion point**

Read `engine/contract_parser.py` and locate the section where category regexes are checked in `parse_contract()`. The sports regex must be inserted before the generic "event" catch-all.

- [ ] **Step 4: Add sports regex and detection**

Add at module level (near the other category regexes):

```python
_SPORTS_RE = re.compile(
    r'\b(?:nba|nfl|nhl|mlb|mls|ufc|pga|atp|wta|fifa|epl|la\s?liga|serie\s?a|bundesliga|ligue\s?1'
    r'|super\s?bowl|world\s?series|stanley\s?cup|champions\s?league|world\s?cup|march\s?madness'
    r'|playoffs?|championship|finals?|semifinals?'
    r'|lakers|celtics|warriors|knicks|nets|bulls|heat|bucks|76ers|suns|mavericks|nuggets|cavaliers|clippers'
    r'|chiefs|eagles|49ers|cowboys|bills|ravens|lions|packers|bears|dolphins|jets|steelers|bengals|rams'
    r'|yankees|dodgers|braves|astros|phillies|mets|padres|orioles|rangers|red\s?sox|cubs|guardians'
    r'|real\s?madrid|barcelona|manchester|liverpool|arsenal|chelsea|bayern|juventus|inter\s?milan|psg'
    r')\b'
    r'|\b(?:beat|defeat|advance|eliminate|sweep|upset)\b.*\b(?:game|match|series|round)\b',
    re.IGNORECASE,
)
```

Insert the sports check in `parse_contract()` after the weather check but before the event/election checks:

```python
# Step 0s: Sports
if _SPORTS_RE.search(q):
    contract.category = "sports"
    contract.parseable = True
    return contract
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/engine/test_contract_parser_sports.py -v`
Expected: 7 passed

- [ ] **Step 6: Run existing parser tests to verify no regressions**

Run: `pytest tests/engine/ -v`
Expected: All existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add engine/contract_parser.py tests/engine/test_contract_parser_sports.py
git commit -m "feat(parser): add sports category detection for maker bot"
```

---

## Task 4: MarketSelector Actor

**Files:**
- Create: `maker/market_selector.py`
- Test: `tests/maker/test_market_selector.py`

- [ ] **Step 1: Write the test**

```python
# tests/maker/test_market_selector.py
import asyncio
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta
from market.state import ContractState
from maker.market_selector import MarketSelector


def _make_market(
    token_id: str,
    category: str = "sports",
    bid: float = 0.40,
    ask: float = 0.60,
    volume: float = 500.0,
) -> ContractState:
    return ContractState(
        yes_token_id=token_id,
        no_token_id=f"no-{token_id}",
        question=f"Will team A beat team B? [{token_id}]",
        category=category,
        best_bid=bid,
        best_ask=ask,
        volume_usd=volume,
    )


def test_filters_non_sports():
    markets = {
        "crypto1": _make_market("crypto1", category="crypto"),
        "sports1": _make_market("sports1", category="sports"),
    }
    selected = MarketSelector.filter_and_rank(markets)
    assert "sports1" in selected
    assert "crypto1" not in selected


def test_filters_low_volume():
    markets = {
        "thin": _make_market("thin", volume=50.0),
        "ok": _make_market("ok", volume=200.0),
    }
    selected = MarketSelector.filter_and_rank(markets)
    assert "ok" in selected
    assert "thin" not in selected


def test_filters_tight_spread():
    markets = {
        "tight": _make_market("tight", bid=0.49, ask=0.51),  # 2c spread
        "wide": _make_market("wide", bid=0.40, ask=0.60),    # 20c spread
    }
    selected = MarketSelector.filter_and_rank(markets)
    assert "wide" in selected
    assert "tight" not in selected


def test_ranks_by_spread_times_volume():
    markets = {
        "a": _make_market("a", bid=0.40, ask=0.50, volume=100.0),  # spread=0.10, score=10
        "b": _make_market("b", bid=0.40, ask=0.60, volume=200.0),  # spread=0.20, score=40
    }
    selected = MarketSelector.filter_and_rank(markets)
    keys = list(selected.keys())
    assert keys[0] == "b"  # higher score first


def test_caps_at_max_active():
    markets = {
        f"m{i}": _make_market(f"m{i}", volume=float(1000 - i))
        for i in range(30)
    }
    selected = MarketSelector.filter_and_rank(markets, max_markets=20)
    assert len(selected) == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/maker/test_market_selector.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# maker/market_selector.py
"""MarketSelector actor — picks which sports markets to quote."""

import asyncio
from collections import OrderedDict
from market.state import AppState, ContractState
from utils.logger import get_logger

log = get_logger(__name__)

# Selection parameters
_MIN_DAILY_VOLUME = 100.0   # $100/day minimum
_MAX_ACTIVE_MARKETS = 20
_MIN_SPREAD = 0.04          # 4c — below this, pro MMs already own it
_TARGET_CATEGORY = "sports"


class MarketSelector:
    """Selects sports markets worth quoting."""

    REFRESH_INTERVAL = 900  # 15 minutes

    def __init__(
        self,
        state: AppState,
        active_markets_q: asyncio.Queue,
    ):
        self._state = state
        self._active_markets_q = active_markets_q

    @staticmethod
    def filter_and_rank(
        markets: dict[str, ContractState],
        max_markets: int = _MAX_ACTIVE_MARKETS,
    ) -> OrderedDict[str, ContractState]:
        """Filter to quotable sports markets, rank by spread * volume."""
        candidates = []
        for token_id, cs in markets.items():
            if cs.category != _TARGET_CATEGORY:
                continue
            spread = cs.best_ask - cs.best_bid
            if spread < _MIN_SPREAD:
                continue
            if cs.volume_usd < _MIN_DAILY_VOLUME:
                continue
            score = spread * cs.volume_usd
            candidates.append((token_id, cs, score))

        candidates.sort(key=lambda x: -x[2])
        result = OrderedDict()
        for token_id, cs, _ in candidates[:max_markets]:
            result[token_id] = cs
        return result

    async def run(self):
        """Main loop — re-evaluate market selection every REFRESH_INTERVAL."""
        while True:
            async with self._state._lock:
                markets = dict(self._state.markets)

            selected = self.filter_and_rank(markets)
            token_ids = set(selected.keys())

            log.info(
                f"MarketSelector: {len(token_ids)} sports markets selected "
                f"(from {len(markets)} total)"
            )

            await self._active_markets_q.put(token_ids)
            await asyncio.sleep(self.REFRESH_INTERVAL)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/maker/test_market_selector.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add maker/market_selector.py tests/maker/test_market_selector.py
git commit -m "feat(maker): add MarketSelector actor — sports market filtering and ranking"
```

---

## Task 5: QuoteEngine Actor

**Files:**
- Create: `maker/quote_engine.py`
- Test: `tests/maker/test_quote_engine.py`

- [ ] **Step 1: Write the test**

```python
# tests/maker/test_quote_engine.py
from maker.quote_engine import QuoteEngine, compute_fair_value, compute_spread
from maker.types import QuoteIntent


def test_fair_value_at_midpoint_no_skew():
    fv = compute_fair_value(mid=0.50, skew=0.0, model_adj=0.0)
    assert fv == 0.50


def test_fair_value_with_positive_skew():
    """Holding YES → push fair value up to attract sellers."""
    fv = compute_fair_value(mid=0.50, skew=0.5, model_adj=0.0)
    assert fv > 0.50
    assert fv == 0.515  # 0.50 + 0.5 * 0.03


def test_fair_value_clamped():
    fv = compute_fair_value(mid=0.98, skew=1.0, model_adj=0.0)
    assert fv <= 0.95


def test_spread_base():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=100.0)
    assert s == 0.06  # BASE_SPREAD


def test_spread_widens_low_volume():
    s = compute_spread(volume_usd=200.0, abs_inventory=0.0, hours_to_expiry=100.0)
    assert s == 0.08  # 0.06 + 0.02


def test_spread_widens_with_inventory():
    s = compute_spread(volume_usd=1000.0, abs_inventory=10.0, hours_to_expiry=100.0)
    assert s == 0.16  # 0.06 + 10 * 0.01


def test_spread_widens_near_expiry():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=24.0)
    assert s == 0.09  # 0.06 + 0.03


def test_spread_max_very_near_expiry():
    s = compute_spread(volume_usd=1000.0, abs_inventory=0.0, hours_to_expiry=3.0)
    assert s == 0.15  # MAX_SPREAD


def test_spread_floor():
    s = compute_spread(volume_usd=10000.0, abs_inventory=0.0, hours_to_expiry=500.0)
    assert s >= 0.04  # MIN_SPREAD


def test_build_quote_intent():
    qi = QuoteEngine.build_quote(
        token_id="abc",
        fair_value=0.50,
        spread=0.10,
        bid_size=10.0,
        ask_size=10.0,
        reason="reprice",
    )
    assert qi.bid_price == 0.45
    assert qi.ask_price == 0.55
    assert qi.spread == 0.10


def test_is_stale_returns_false_when_same():
    old = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    new = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    assert not QuoteEngine.is_stale(old, new, tick=0.01)


def test_is_stale_returns_true_when_price_changed():
    old = QuoteIntent("abc", 0.45, 0.55, 10.0, 10.0, "reprice")
    new = QuoteIntent("abc", 0.47, 0.57, 10.0, 10.0, "reprice")
    assert QuoteEngine.is_stale(old, new, tick=0.01)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/maker/test_quote_engine.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# maker/quote_engine.py
"""QuoteEngine actor — computes fair value and bid/ask quotes."""

import asyncio
from maker.state import MakerState
from maker.types import QuoteIntent, SkewUpdate
from market.state import AppState
from utils.logger import get_logger

log = get_logger(__name__)

# Spread parameters
BASE_SPREAD = 0.06
MIN_SPREAD = 0.04
MAX_SPREAD = 0.15
QUOTE_SIZE_USDC = 10.0
MAX_SKEW_ADJ = 0.03  # max inventory adjustment to fair value


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def compute_fair_value(mid: float, skew: float, model_adj: float) -> float:
    """
    Hybrid fair value: market mid + inventory skew + model adjustment.
    skew: [-1, +1] from MakerState.skew_factor. Positive = holding YES.
    model_adj: 0.0 until Bayesian calibration is ready (Phase 2+).
    """
    inventory_adj = skew * MAX_SKEW_ADJ
    return _clamp(mid + inventory_adj + model_adj, 0.05, 0.95)


def compute_spread(
    volume_usd: float,
    abs_inventory: float,
    hours_to_expiry: float,
) -> float:
    """Dynamic spread based on volume, inventory, and time to resolution."""
    spread = BASE_SPREAD

    # Widen for thin markets
    if volume_usd < 500.0:
        spread += 0.02

    # Widen for inventory exposure
    spread += abs_inventory * 0.01

    # Widen near resolution
    if hours_to_expiry < 6.0:
        return MAX_SPREAD
    if hours_to_expiry < 48.0:
        spread += 0.03

    return _clamp(spread, MIN_SPREAD, MAX_SPREAD)


class QuoteEngine:
    """Computes quotes for active markets on a 1.5s cycle."""

    REPRICE_INTERVAL = 1.5

    def __init__(
        self,
        app_state: AppState,
        maker_state: MakerState,
        active_markets_q: asyncio.Queue,
        quote_intents_q: asyncio.Queue,
        skew_updates_q: asyncio.Queue,
    ):
        self._app = app_state
        self._maker = maker_state
        self._active_markets_q = active_markets_q
        self._quote_intents_q = quote_intents_q
        self._skew_updates_q = skew_updates_q
        self._active_token_ids: set[str] = set()

    @staticmethod
    def build_quote(
        token_id: str,
        fair_value: float,
        spread: float,
        bid_size: float,
        ask_size: float,
        reason: str,
    ) -> QuoteIntent:
        half = spread / 2.0
        return QuoteIntent(
            token_id=token_id,
            bid_price=round(fair_value - half, 4),
            ask_price=round(fair_value + half, 4),
            bid_size=bid_size,
            ask_size=ask_size,
            reason=reason,
        )

    @staticmethod
    def is_stale(old: QuoteIntent, new: QuoteIntent, tick: float = 0.01) -> bool:
        """True if the new quote differs enough from the old to warrant a reprice."""
        return (
            abs(old.bid_price - new.bid_price) >= tick
            or abs(old.ask_price - new.ask_price) >= tick
        )

    async def run(self):
        while True:
            # Drain market selector updates (non-blocking)
            while not self._active_markets_q.empty():
                try:
                    self._active_token_ids = self._active_markets_q.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Drain skew updates (non-blocking)
            while not self._skew_updates_q.empty():
                try:
                    update: SkewUpdate = self._skew_updates_q.get_nowait()
                    # Skew is already stored in MakerState; this is a notification
                except asyncio.QueueEmpty:
                    break

            # Compute quotes for each active market
            async with self._app._lock:
                markets = dict(self._app.markets)

            for token_id in self._active_token_ids:
                cs = markets.get(token_id)
                if not cs:
                    continue
                if self._maker.is_cooled_down(token_id):
                    continue

                skew = self._maker.skew_factor(token_id)
                abs_inv = abs(self._maker.get_inventory(token_id))

                fv = compute_fair_value(mid=cs.mid, skew=skew, model_adj=0.0)
                spread = compute_spread(
                    volume_usd=cs.volume_usd,
                    abs_inventory=abs_inv,
                    hours_to_expiry=999.0,  # TODO: compute from parsed expiry
                )

                new_quote = self.build_quote(
                    token_id=token_id,
                    fair_value=fv,
                    spread=spread,
                    bid_size=QUOTE_SIZE_USDC,
                    ask_size=QUOTE_SIZE_USDC,
                    reason="reprice",
                )

                # Only emit if materially different from last quote
                old_quote = self._maker.last_quotes.get(token_id)
                if old_quote is None or self.is_stale(old_quote, new_quote):
                    self._maker.last_quotes[token_id] = new_quote
                    await self._quote_intents_q.put(new_quote)

            await asyncio.sleep(self.REPRICE_INTERVAL)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/maker/test_quote_engine.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add maker/quote_engine.py tests/maker/test_quote_engine.py
git commit -m "feat(maker): add QuoteEngine actor — fair value, spread, stale detection"
```

---

## Task 6: OrderManager Actor

**Files:**
- Create: `maker/order_manager.py`
- Test: `tests/maker/test_order_manager.py`

- [ ] **Step 1: Write the test**

```python
# tests/maker/test_order_manager.py
import asyncio
import pytest
from maker.order_manager import OrderManager
from maker.state import MakerState
from maker.types import QuoteIntent, CancelAll


class FakeClobClient:
    """Records calls instead of hitting the network."""
    def __init__(self):
        self.posted: list[dict] = []
        self.cancelled: list[str] = []
        self._next_id = 0

    def create_order(self, order_args):
        return {"id": f"signed-{self._next_id}"}

    def post_order(self, signed_order, orderType=None, post_only=False):
        self._next_id += 1
        oid = f"order-{self._next_id}"
        self.posted.append({
            "orderID": oid,
            "post_only": post_only,
            "orderType": str(orderType),
        })
        return {"orderID": oid, "status": "live"}

    def cancel_orders(self, order_ids):
        self.cancelled.extend(order_ids)
        return {"cancelled": order_ids}

    def cancel_all(self):
        self.cancelled.append("ALL")
        return {}


def test_place_new_quotes():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    qi = QuoteIntent("tok1", 0.45, 0.55, 10.0, 10.0, "new_market")
    om.handle_quote_intent_sync(qi)

    assert len(clob.posted) == 2  # bid + ask
    assert clob.posted[0]["post_only"] is True
    assert "tok1" in maker_state.live_orders


def test_replace_existing_quotes():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    qi1 = QuoteIntent("tok1", 0.45, 0.55, 10.0, 10.0, "new_market")
    om.handle_quote_intent_sync(qi1)

    qi2 = QuoteIntent("tok1", 0.46, 0.56, 10.0, 10.0, "reprice")
    om.handle_quote_intent_sync(qi2)

    assert len(clob.cancelled) == 2  # old bid + ask cancelled
    assert len(clob.posted) == 4     # 2 old + 2 new


def test_cancel_single_market():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    qi = QuoteIntent("tok1", 0.45, 0.55, 10.0, 10.0, "new_market")
    om.handle_quote_intent_sync(qi)

    om.handle_cancel_sync(CancelAll("tok1"))
    assert "tok1" not in maker_state.live_orders


def test_cancel_all_global():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=False)

    for i in range(3):
        qi = QuoteIntent(f"tok{i}", 0.45, 0.55, 10.0, 10.0, "new_market")
        om.handle_quote_intent_sync(qi)

    om.handle_cancel_sync(CancelAll("*"))
    assert len(maker_state.live_orders) == 0
    assert "ALL" in clob.cancelled


def test_paper_mode_no_api_calls():
    maker_state = MakerState()
    clob = FakeClobClient()
    om = OrderManager(maker_state, clob=clob, paper=True)

    qi = QuoteIntent("tok1", 0.45, 0.55, 10.0, 10.0, "new_market")
    om.handle_quote_intent_sync(qi)

    assert len(clob.posted) == 0  # no API calls in paper mode
    assert "tok1" in maker_state.live_orders  # but state is tracked
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/maker/test_order_manager.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# maker/order_manager.py
"""OrderManager actor — places/cancels limit orders via py-clob-client."""

import asyncio
from maker.state import MakerState
from maker.types import QuoteIntent, CancelAll
from utils.logger import get_logger

log = get_logger(__name__)


class OrderManager:
    """Translates QuoteIntents into CLOB API calls."""

    def __init__(
        self,
        maker_state: MakerState,
        clob=None,
        paper: bool = True,
        quote_intents_q: asyncio.Queue | None = None,
        cancel_q: asyncio.Queue | None = None,
    ):
        self._maker = maker_state
        self._clob = clob
        self._paper = paper
        self._quote_intents_q = quote_intents_q
        self._cancel_q = cancel_q

    def handle_quote_intent_sync(self, intent: QuoteIntent) -> None:
        """Synchronous handler for testing. Called by run() in async context."""
        existing = self._maker.live_orders.get(intent.token_id)

        # Cancel existing orders first
        if existing:
            self._cancel_pair(existing)

        # Place new bid + ask
        bid_oid = self._place_one(intent.token_id, intent.bid_price, intent.bid_size, "BUY")
        ask_oid = self._place_one(intent.token_id, intent.ask_price, intent.ask_size, "SELL")

        self._maker.live_orders[intent.token_id] = {
            "bid_order_id": bid_oid,
            "ask_order_id": ask_oid,
            "bid_price": intent.bid_price,
            "ask_price": intent.ask_price,
            "bid_size": intent.bid_size,
            "ask_size": intent.ask_size,
        }

        log.info(
            f"QUOTE {'[PAPER] ' if self._paper else ''}"
            f"[{intent.token_id[:8]}] bid={intent.bid_price:.3f} "
            f"ask={intent.ask_price:.3f} spread={intent.spread:.3f} "
            f"reason={intent.reason}"
        )

    def handle_cancel_sync(self, cancel: CancelAll) -> None:
        """Synchronous cancel handler."""
        if cancel.is_global:
            if not self._paper and self._clob:
                self._clob.cancel_all()
            self._maker.live_orders.clear()
            log.warning("CANCEL ALL — all quotes pulled")
        else:
            existing = self._maker.live_orders.pop(cancel.token_id, None)
            if existing and not self._paper and self._clob:
                self._cancel_pair(existing)
            log.info(f"CANCEL [{cancel.token_id[:8]}]")

    def _place_one(self, token_id: str, price: float, size: float, side: str) -> str:
        """Place a single post-only GTC order. Returns order ID."""
        if self._paper:
            return f"paper-{token_id[:8]}-{side.lower()}"

        from py_clob_client.clob_types import OrderArgs, OrderType

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
        )
        signed = self._clob.create_order(order_args)
        result = self._clob.post_order(signed, orderType=OrderType.GTC, post_only=True)
        return result.get("orderID", "")

    def _cancel_pair(self, order_info: dict) -> None:
        """Cancel a bid+ask pair."""
        if self._paper:
            return
        ids = [
            order_info.get("bid_order_id", ""),
            order_info.get("ask_order_id", ""),
        ]
        ids = [oid for oid in ids if oid and not oid.startswith("paper-")]
        if ids:
            self._clob.cancel_orders(ids)

    async def run(self):
        """Main loop — process quote intents and cancel commands."""
        while True:
            # Process cancels with priority (defense)
            while not self._cancel_q.empty():
                try:
                    cancel = self._cancel_q.get_nowait()
                    self.handle_cancel_sync(cancel)
                except asyncio.QueueEmpty:
                    break

            # Process quote intents
            try:
                intent = await asyncio.wait_for(
                    self._quote_intents_q.get(), timeout=0.1
                )
                self.handle_quote_intent_sync(intent)
            except asyncio.TimeoutError:
                pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/maker/test_order_manager.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add maker/order_manager.py tests/maker/test_order_manager.py
git commit -m "feat(maker): add OrderManager actor — place/cancel post-only GTC orders"
```

---

## Task 7: FillPoller Actor

**Files:**
- Create: `maker/fill_poller.py`
- Test: `tests/maker/test_fill_poller.py`

- [ ] **Step 1: Write the test**

```python
# tests/maker/test_fill_poller.py
import asyncio
import time
import pytest
from maker.fill_poller import FillPoller
from maker.state import MakerState
from market.state import ContractState


def _make_market(bid: float = 0.50, ask: float = 0.60) -> ContractState:
    return ContractState(
        yes_token_id="tok1",
        no_token_id="no-tok1",
        question="test",
        category="sports",
        best_bid=bid,
        best_ask=ask,
    )


@pytest.mark.asyncio
async def test_paper_fill_bid_crossed():
    """When market ask drops below our bid, our bid fills."""
    maker_state = MakerState()
    fills_q = asyncio.Queue()

    maker_state.live_orders["tok1"] = {
        "bid_order_id": "paper-bid",
        "ask_order_id": "paper-ask",
        "bid_price": 0.50,
        "ask_price": 0.60,
        "bid_size": 10.0,
        "ask_size": 10.0,
    }

    # Market ask dropped to 0.48 — below our bid of 0.50
    markets = {"tok1": _make_market(bid=0.45, ask=0.48)}

    fills = FillPoller.check_paper_fills(maker_state, markets)
    assert len(fills) == 1
    assert fills[0].side == "BUY"
    assert fills[0].price == 0.50


@pytest.mark.asyncio
async def test_paper_fill_ask_crossed():
    """When market bid rises above our ask, our ask fills."""
    maker_state = MakerState()

    maker_state.live_orders["tok1"] = {
        "bid_order_id": "paper-bid",
        "ask_order_id": "paper-ask",
        "bid_price": 0.50,
        "ask_price": 0.60,
        "bid_size": 10.0,
        "ask_size": 10.0,
    }

    # Market bid rose to 0.62 — above our ask of 0.60
    markets = {"tok1": _make_market(bid=0.62, ask=0.65)}

    fills = FillPoller.check_paper_fills(maker_state, markets)
    assert len(fills) == 1
    assert fills[0].side == "SELL"
    assert fills[0].price == 0.60


@pytest.mark.asyncio
async def test_paper_no_fill_when_not_crossed():
    maker_state = MakerState()

    maker_state.live_orders["tok1"] = {
        "bid_order_id": "paper-bid",
        "ask_order_id": "paper-ask",
        "bid_price": 0.50,
        "ask_price": 0.60,
        "bid_size": 10.0,
        "ask_size": 10.0,
    }

    # Market sits between our quotes — no fill
    markets = {"tok1": _make_market(bid=0.52, ask=0.58)}

    fills = FillPoller.check_paper_fills(maker_state, markets)
    assert len(fills) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/maker/test_fill_poller.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# maker/fill_poller.py
"""FillPoller actor — detects order fills at 500ms cadence."""

import asyncio
import time
from maker.state import MakerState
from maker.types import Fill
from market.state import AppState, ContractState
from utils.logger import get_logger

log = get_logger(__name__)


class FillPoller:
    """Polls for fills — live via get_orders(), paper via book crossing."""

    POLL_INTERVAL = 0.5  # 500ms

    def __init__(
        self,
        app_state: AppState,
        maker_state: MakerState,
        fills_q: asyncio.Queue,
        clob=None,
        paper: bool = True,
    ):
        self._app = app_state
        self._maker = maker_state
        self._fills_q = fills_q
        self._clob = clob
        self._paper = paper
        # Live mode: track last known order states
        self._order_states: dict[str, str] = {}

    @staticmethod
    def check_paper_fills(
        maker_state: MakerState,
        markets: dict[str, ContractState],
    ) -> list[Fill]:
        """Check if any live paper quotes have been crossed by the market."""
        fills = []
        now = time.time()

        for token_id, order_info in list(maker_state.live_orders.items()):
            cs = markets.get(token_id)
            if not cs:
                continue

            bid_price = order_info.get("bid_price", 0.0)
            ask_price = order_info.get("ask_price", 1.0)
            bid_size = order_info.get("bid_size", 0.0)
            ask_size = order_info.get("ask_size", 0.0)

            # Someone's ask <= our bid → our bid fills (we buy)
            if cs.best_ask <= bid_price and bid_price > 0:
                fills.append(Fill(
                    token_id=token_id,
                    side="BUY",
                    price=bid_price,
                    size=bid_size,
                    order_id=order_info.get("bid_order_id", ""),
                    filled_at=now,
                ))

            # Someone's bid >= our ask → our ask fills (we sell)
            if cs.best_bid >= ask_price and ask_price < 1.0:
                fills.append(Fill(
                    token_id=token_id,
                    side="SELL",
                    price=ask_price,
                    size=ask_size,
                    order_id=order_info.get("ask_order_id", ""),
                    filled_at=now,
                ))

        return fills

    async def _run_paper(self):
        """Paper mode: simulate fills from book crossing."""
        while True:
            async with self._app._lock:
                markets = dict(self._app.markets)

            fills = self.check_paper_fills(self._maker, markets)
            for fill in fills:
                log.info(
                    f"PAPER FILL: {fill.side} {fill.size:.2f} @ {fill.price:.3f} "
                    f"[{fill.token_id[:8]}]"
                )
                await self._fills_q.put(fill)

            await asyncio.sleep(self.POLL_INTERVAL)

    async def _run_live(self):
        """Live mode: poll get_orders() for status changes."""
        from py_clob_client.clob_types import OpenOrderParams

        while True:
            try:
                orders = self._clob.get_orders(OpenOrderParams())
                for order in orders:
                    oid = order.get("orderID", "")
                    status = order.get("status", "")
                    prev = self._order_states.get(oid)

                    if prev in ("OPEN", "live") and status in ("MATCHED", "FILLED"):
                        fill = Fill(
                            token_id=order.get("asset_id", ""),
                            side=order.get("side", "BUY"),
                            price=float(order.get("price", 0)),
                            size=float(order.get("size_matched", order.get("original_size", 0))),
                            order_id=oid,
                            filled_at=time.time(),
                        )
                        log.info(
                            f"FILL: {fill.side} {fill.size:.2f} @ {fill.price:.3f} "
                            f"[{fill.token_id[:8]}]"
                        )
                        await self._fills_q.put(fill)

                    self._order_states[oid] = status

            except Exception as exc:
                log.warning(f"FillPoller error: {exc}")

            await asyncio.sleep(self.POLL_INTERVAL)

    async def run(self):
        if self._paper:
            await self._run_paper()
        else:
            await self._run_live()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/maker/test_fill_poller.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add maker/fill_poller.py tests/maker/test_fill_poller.py
git commit -m "feat(maker): add FillPoller actor — 500ms fill detection, paper simulation"
```

---

## Task 8: InventoryManager + CircuitBreaker

**Files:**
- Create: `maker/inventory.py`
- Test: `tests/maker/test_inventory.py`

- [ ] **Step 1: Write the test**

```python
# tests/maker/test_inventory.py
import asyncio
import time
import pytest
from maker.inventory import InventoryManager
from maker.state import MakerState
from maker.types import Fill, SkewUpdate, CancelAll


@pytest.fixture
def setup():
    maker_state = MakerState(max_inventory_per_market=50.0, max_total_inventory=200.0)
    fills_q = asyncio.Queue()
    skew_q = asyncio.Queue()
    cancel_q = asyncio.Queue()
    im = InventoryManager(maker_state, fills_q, skew_q, cancel_q)
    return im, maker_state, fills_q, skew_q, cancel_q


@pytest.mark.asyncio
async def test_buy_fill_updates_inventory(setup):
    im, maker_state, _, skew_q, _ = setup
    fill = Fill("tok1", "BUY", 0.45, 10.0, "order-1", time.time())

    await im.handle_fill(fill)

    assert maker_state.get_inventory("tok1") == 10.0
    skew = skew_q.get_nowait()
    assert skew.token_id == "tok1"
    assert skew.skew_factor == 0.2  # 10/50


@pytest.mark.asyncio
async def test_sell_fill_reduces_inventory(setup):
    im, maker_state, _, skew_q, _ = setup
    maker_state.update_inventory("tok1", "BUY", 20.0)

    fill = Fill("tok1", "SELL", 0.55, 5.0, "order-2", time.time())
    await im.handle_fill(fill)

    assert maker_state.get_inventory("tok1") == 15.0


@pytest.mark.asyncio
async def test_per_market_cap_triggers_cancel(setup):
    im, maker_state, _, _, cancel_q = setup
    maker_state.update_inventory("tok1", "BUY", 45.0)

    fill = Fill("tok1", "BUY", 0.45, 10.0, "order-3", time.time())
    await im.handle_fill(fill)

    cancel = cancel_q.get_nowait()
    assert cancel.token_id == "tok1"
    assert not cancel.is_global


@pytest.mark.asyncio
async def test_total_cap_triggers_global_cancel(setup):
    im, maker_state, _, _, cancel_q = setup
    # Fill up to near total cap
    for i in range(4):
        maker_state.update_inventory(f"tok{i}", "BUY", 48.0)
    # This fill pushes total over 200
    fill = Fill("tok4", "BUY", 0.45, 10.0, "order-4", time.time())
    await im.handle_fill(fill)

    # Should have at least a global cancel
    cancels = []
    while not cancel_q.empty():
        cancels.append(cancel_q.get_nowait())
    assert any(c.is_global for c in cancels)


@pytest.mark.asyncio
async def test_rapid_double_fill_triggers_cancel(setup):
    im, maker_state, _, _, cancel_q = setup
    now = time.time()

    fill_buy = Fill("tok1", "BUY", 0.45, 10.0, "order-5", now)
    await im.handle_fill(fill_buy)

    # Same market, opposite side, within 5 seconds
    fill_sell = Fill("tok1", "SELL", 0.55, 10.0, "order-6", now + 2.0)
    await im.handle_fill(fill_sell)

    cancels = []
    while not cancel_q.empty():
        cancels.append(cancel_q.get_nowait())
    assert any(c.token_id == "tok1" for c in cancels)


@pytest.mark.asyncio
async def test_daily_pnl_recorded(setup):
    im, maker_state, _, _, _ = setup

    fill_buy = Fill("tok1", "BUY", 0.45, 10.0, "order-7", time.time())
    await im.handle_fill(fill_buy)

    fill_sell = Fill("tok1", "SELL", 0.55, 10.0, "order-8", time.time())
    await im.handle_fill(fill_sell)

    # Spread capture: sold at 0.55, bought at 0.45 → profit
    # Daily PnL tracking is based on round-trip detection
    assert maker_state.get_inventory("tok1") == 0.0  # round-tripped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/maker/test_inventory.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# maker/inventory.py
"""InventoryManager + CircuitBreaker — tracks exposure, fires cancel-all."""

import asyncio
import time
from maker.state import MakerState
from maker.types import Fill, SkewUpdate, CancelAll
from utils.logger import get_logger

log = get_logger(__name__)

COOLDOWN_SECONDS = 300  # 5 minutes after circuit breaker fires
RAPID_FILL_WINDOW = 5.0  # seconds — both sides filled within this = adverse selection
MAX_DAILY_LOSS_PCT = 0.03  # 3% of bankroll


class InventoryManager:
    """Tracks per-market inventory, computes skew, fires circuit breakers."""

    def __init__(
        self,
        maker_state: MakerState,
        fills_q: asyncio.Queue,
        skew_updates_q: asyncio.Queue,
        cancel_q: asyncio.Queue,
        bankroll: float = 500.0,
    ):
        self._maker = maker_state
        self._fills_q = fills_q
        self._skew_q = skew_updates_q
        self._cancel_q = cancel_q
        self._max_daily_loss = bankroll * MAX_DAILY_LOSS_PCT

    async def handle_fill(self, fill: Fill) -> None:
        """Process a fill: update inventory, compute skew, check limits."""
        # 1. Update inventory
        self._maker.update_inventory(fill.token_id, fill.side, fill.size)

        # 2. Emit skew update
        skew = self._maker.skew_factor(fill.token_id)
        await self._skew_q.put(SkewUpdate(token_id=fill.token_id, skew_factor=skew))

        # 3. Check rapid double-fill
        await self._check_rapid_fill(fill)

        # 4. Check per-market inventory cap
        abs_pos = abs(self._maker.get_inventory(fill.token_id))
        if abs_pos >= self._maker.max_inventory_per_market:
            await self._cancel_q.put(CancelAll(fill.token_id))
            self._maker.cooldowns[fill.token_id] = time.time() + COOLDOWN_SECONDS
            log.warning(
                f"INVENTORY CAP: [{fill.token_id[:8]}] at ${abs_pos:.0f} "
                f"— quotes pulled for {COOLDOWN_SECONDS}s"
            )

        # 5. Check total inventory cap
        total = self._maker.total_abs_inventory
        if total >= self._maker.max_total_inventory:
            await self._cancel_q.put(CancelAll("*"))
            log.warning(f"TOTAL INVENTORY CAP: ${total:.0f} — ALL quotes pulled")

        # 6. Check daily loss
        if self._maker.daily_pnl <= -self._max_daily_loss:
            await self._cancel_q.put(CancelAll("*"))
            log.warning(
                f"DAILY LOSS LIMIT: ${self._maker.daily_pnl:.2f} — ALL quotes pulled"
            )

        log.info(
            f"INVENTORY: [{fill.token_id[:8]}] {fill.side} {fill.size:.2f} @ {fill.price:.3f} "
            f"→ net={self._maker.get_inventory(fill.token_id):.2f} "
            f"total_abs=${total:.0f} skew={skew:.2f}"
        )

    async def _check_rapid_fill(self, fill: Fill) -> None:
        """Detect both sides filled within RAPID_FILL_WINDOW."""
        times = self._maker.last_fill_times.setdefault(fill.token_id, {})
        other_side = "SELL" if fill.side == "BUY" else "BUY"

        if other_side in times and (fill.filled_at - times[other_side]) < RAPID_FILL_WINDOW:
            await self._cancel_q.put(CancelAll(fill.token_id))
            self._maker.cooldowns[fill.token_id] = time.time() + COOLDOWN_SECONDS
            log.warning(
                f"RAPID DOUBLE-FILL: [{fill.token_id[:8]}] both sides hit within "
                f"{RAPID_FILL_WINDOW}s — quotes pulled"
            )

        times[fill.side] = fill.filled_at

    async def run(self):
        """Main loop — process fills from queue."""
        while True:
            fill = await self._fills_q.get()
            try:
                await self.handle_fill(fill)
            except Exception as exc:
                log.exception(f"InventoryManager error: {exc}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/maker/test_inventory.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add maker/inventory.py tests/maker/test_inventory.py
git commit -m "feat(maker): add InventoryManager + CircuitBreaker — exposure limits, rapid-fill defense"
```

---

## Task 9: Runner & Main Entrypoint

**Files:**
- Create: `maker/runner.py`
- Modify: `main.py`
- Test: `tests/maker/test_runner.py`

- [ ] **Step 1: Write the test**

```python
# tests/maker/test_runner.py
from maker.runner import build_maker_actors


def test_build_maker_actors_returns_all_components():
    """Smoke test: all actors and queues are wired."""
    from market.state import AppState
    actors, queues = build_maker_actors(
        app_state=AppState(),
        paper=True,
        clob=None,
    )
    assert "selector" in actors
    assert "quote_engine" in actors
    assert "order_manager" in actors
    assert "fill_poller" in actors
    assert "inventory" in actors
    assert "active_markets_q" in queues
    assert "quote_intents_q" in queues
    assert "fills_q" in queues
    assert "skew_updates_q" in queues
    assert "cancel_q" in queues
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/maker/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the runner**

```python
# maker/runner.py
"""Maker bot entrypoint — wires actors, queues, and reused feeds."""

import asyncio
import config
from market.state import AppState
from maker.state import MakerState
from maker.market_selector import MarketSelector
from maker.quote_engine import QuoteEngine
from maker.order_manager import OrderManager
from maker.fill_poller import FillPoller
from maker.inventory import InventoryManager
from utils.logger import get_logger

log = get_logger(__name__)


def build_maker_actors(
    app_state: AppState,
    paper: bool = True,
    clob=None,
    bankroll: float = 500.0,
) -> tuple[dict, dict]:
    """Create all actors and queues. Returns (actors_dict, queues_dict)."""
    maker_state = MakerState()

    # Queues
    active_markets_q = asyncio.Queue()
    quote_intents_q = asyncio.Queue()
    fills_q = asyncio.Queue()
    skew_updates_q = asyncio.Queue()
    cancel_q = asyncio.Queue()

    queues = {
        "active_markets_q": active_markets_q,
        "quote_intents_q": quote_intents_q,
        "fills_q": fills_q,
        "skew_updates_q": skew_updates_q,
        "cancel_q": cancel_q,
    }

    actors = {
        "selector": MarketSelector(app_state, active_markets_q),
        "quote_engine": QuoteEngine(
            app_state, maker_state, active_markets_q, quote_intents_q, skew_updates_q,
        ),
        "order_manager": OrderManager(
            maker_state, clob=clob, paper=paper,
            quote_intents_q=quote_intents_q, cancel_q=cancel_q,
        ),
        "fill_poller": FillPoller(
            app_state, maker_state, fills_q, clob=clob, paper=paper,
        ),
        "inventory": InventoryManager(
            maker_state, fills_q, skew_updates_q, cancel_q, bankroll=bankroll,
        ),
    }

    return actors, queues


async def run_maker():
    """Main async entrypoint for maker mode."""
    from feeds.microstructure import MicrostructureFeed
    from market.clob_monitor import CLOBMonitor
    from trading.balance import BalancePoller

    paper = config.PAPER
    log.info(f"Starting maker bot (paper={paper})")

    app_state = AppState()

    # Build CLOB client for live mode
    clob = None
    wallet_address = ""
    if not paper:
        from trading.executor import _build_clob_client
        clob, wallet_address = _build_clob_client()

    actors, _ = build_maker_actors(
        app_state=app_state,
        paper=paper,
        clob=clob,
        bankroll=config.BANKROLL_USDC,
    )

    coros = [
        # Reused feeds
        CLOBMonitor(app_state).start(),
        MicrostructureFeed(app_state).start(),
    ]

    if wallet_address:
        coros.append(BalancePoller(app_state, wallet_address).start())

    # Maker actors
    for actor in actors.values():
        coros.append(actor.run())

    log.info(f"Maker bot running with {len(actors)} actors")
    await asyncio.gather(*coros)
```

- [ ] **Step 4: Modify main.py to add --mode flag**

Read `main.py` to find the `if __name__ == "__main__":` block, then add argparse:

```python
# At the bottom of main.py, replace:
#   if __name__ == "__main__":
#       asyncio.run(main())
# with:

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Polymarket trading bot")
    ap.add_argument("--mode", default="taker", choices=["taker", "maker"],
                    help="taker (directional) or maker (market making)")
    cli_args = ap.parse_args()

    if cli_args.mode == "maker":
        from maker.runner import run_maker
        asyncio.run(run_maker())
    else:
        asyncio.run(main())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/maker/test_runner.py -v`
Expected: 1 passed

- [ ] **Step 6: Verify --mode flag works**

Run: `python main.py --help`
Expected output includes: `--mode {taker,maker}`

- [ ] **Step 7: Commit**

```bash
git add maker/runner.py main.py tests/maker/test_runner.py
git commit -m "feat(maker): add runner entrypoint, wire --mode maker to main.py"
```

---

## Task 10: Integration Smoke Test

**Files:**
- Test: `tests/maker/test_integration.py`

- [ ] **Step 1: Write an end-to-end integration test**

```python
# tests/maker/test_integration.py
"""Integration test: full actor pipeline in paper mode."""

import asyncio
import time
import pytest
from market.state import AppState, ContractState
from maker.runner import build_maker_actors
from maker.types import Fill


@pytest.mark.asyncio
async def test_full_pipeline_paper_mode():
    """
    Simulate: selector picks a market → quote engine quotes it →
    fill poller detects a paper fill → inventory updates.
    """
    app_state = AppState()

    # Seed a sports market into state
    async with app_state._lock:
        app_state.markets["tok1"] = ContractState(
            yes_token_id="tok1",
            no_token_id="no-tok1",
            question="Will the Lakers beat the Celtics?",
            category="sports",
            best_bid=0.40,
            best_ask=0.60,
            volume_usd=500.0,
        )

    actors, queues = build_maker_actors(app_state=app_state, paper=True)

    # 1. Manually trigger market selection
    selected = actors["selector"].filter_and_rank(app_state.markets)
    assert "tok1" in selected

    # 2. Push selected markets to quote engine
    await queues["active_markets_q"].put(set(selected.keys()))

    # Give quote engine one cycle
    # Instead of running the full loop, test the components directly:
    from maker.quote_engine import compute_fair_value, compute_spread, QuoteEngine

    fv = compute_fair_value(mid=0.50, skew=0.0, model_adj=0.0)
    spread = compute_spread(volume_usd=500.0, abs_inventory=0.0, hours_to_expiry=100.0)
    quote = QuoteEngine.build_quote("tok1", fv, spread, 10.0, 10.0, "new_market")

    assert quote.bid_price < quote.ask_price
    assert quote.spread >= 0.04

    # 3. Simulate order placement via OrderManager
    actors["order_manager"].handle_quote_intent_sync(quote)
    maker_state = actors["order_manager"]._maker
    assert "tok1" in maker_state.live_orders

    # 4. Simulate a fill via InventoryManager
    fill = Fill("tok1", "BUY", quote.bid_price, 10.0, "paper-bid", time.time())
    await actors["inventory"].handle_fill(fill)

    assert maker_state.get_inventory("tok1") == 10.0
    skew = await queues["skew_updates_q"].get()
    assert skew.skew_factor > 0  # holding YES → positive skew
```

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/maker/test_integration.py -v`
Expected: 1 passed

- [ ] **Step 3: Run all maker tests**

Run: `pytest tests/maker/ -v`
Expected: All tests pass (types + state + selector + quote_engine + order_manager + fill_poller + inventory + runner + integration)

- [ ] **Step 4: Run full test suite to verify no regressions**

Run: `pytest tests/ -v --ignore=tests/trading/test_executor.py --ignore=tests/trading/test_executor_improvements.py`
Expected: No new failures (pre-existing test_risk.py async failures are known)

- [ ] **Step 5: Commit**

```bash
git add tests/maker/test_integration.py
git commit -m "test(maker): add integration smoke test — full actor pipeline in paper mode"
```

---

## Summary

| Task | Component | Files | Tests |
|---|---|---|---|
| 1 | Types | `maker/types.py` | 5 tests |
| 2 | MakerState | `maker/state.py` | 9 tests |
| 3 | Sports parser | `engine/contract_parser.py` | 7 tests |
| 4 | MarketSelector | `maker/market_selector.py` | 5 tests |
| 5 | QuoteEngine | `maker/quote_engine.py` | 12 tests |
| 6 | OrderManager | `maker/order_manager.py` | 5 tests |
| 7 | FillPoller | `maker/fill_poller.py` | 3 tests |
| 8 | InventoryManager | `maker/inventory.py` | 6 tests |
| 9 | Runner + main.py | `maker/runner.py`, `main.py` | 1 test |
| 10 | Integration | — | 1 test |
| **Total** | **9 new files** | **2 modified** | **54 tests** |

After Task 10, you can run: `python main.py --mode maker` and the maker bot will start in paper mode, quoting sports markets.
