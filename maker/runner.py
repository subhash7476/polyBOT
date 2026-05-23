# maker/runner.py
"""Maker bot entrypoint — wires actors, queues, and reused feeds."""

import asyncio
import os
import time
import config
from market.state import AppState
from maker.state import MakerState
from maker.types import CancelAll
from maker.market_selector import MarketSelector
from maker.quote_engine import QuoteEngine
from maker.order_manager import OrderManager
from maker.fill_poller import FillPoller
from maker.shadow_fill_poller import ShadowFillPoller
from maker.inventory import InventoryManager
from maker.markout_tracker import MarkoutTracker
from maker.overnight_viability import OvernightViabilityMonitor
from maker.fill_ledger import FillLedger
from maker.vpin_poller import VPINPoller
from maker.state_persistence import MakerCheckpointer, MakerStateLoader, LifetimeStatsCache
from maker.liveness import (
    LivenessState, AlertDispatcher, LivenessMonitor, FileSink, StdoutSink, TelegramSink,
)
from trading.redeemall import redeemall_loop
from utils.logger import get_logger

log = get_logger(__name__)


async def _run_vpin_poller(poller: VPINPoller) -> None:
    """Isolated wrapper — VPINPoller crash does not propagate to asyncio.gather."""
    while True:
        try:
            await poller.run()
        except Exception as exc:
            log.exception(f"VPINPoller crashed (restarting in 30s): {exc}")
            await asyncio.sleep(30.0)


async def _balance_guard_loop(
    app_state: AppState,
    maker_state: MakerState,
    cancel_q: asyncio.Queue,
    min_balance: float,
    interval: float = 60.0,
) -> None:
    """Pull all quotes and pause quoting when on-chain USDC balance is too low.

    Only fires once BalancePoller has completed its first fetch (balance > 0).
    Rechecks every `interval` seconds — quoting resumes automatically when
    balance recovers (e.g. after redemption or a manual top-up).
    """
    triggered = False
    while True:
        await asyncio.sleep(interval)
        balance = app_state.balance.current
        if balance <= 0.0:
            continue  # not yet polled — don't gate on startup noise

        if balance < min_balance:
            if not triggered:
                log.warning(
                    f"LOW BALANCE: ${balance:.2f} < minimum ${min_balance:.2f} — "
                    f"pulling all quotes and pausing for {interval:.0f}s"
                )
                triggered = True
            await cancel_q.put(CancelAll("*"))
            maker_state.global_cooldown_until = time.time() + interval
        else:
            if triggered:
                log.info(f"Balance recovered: ${balance:.2f} — resuming quoting")
                triggered = False


def _extract_heartbeat_id(value: object) -> str:
    """Best-effort extraction from SDK dicts or API error bodies."""
    if isinstance(value, dict):
        heartbeat_id = value.get("heartbeat_id")
        return str(heartbeat_id) if heartbeat_id else ""

    import re

    msg = str(value)
    patterns = (
        r'["\']heartbeat_id["\']\s*:\s*["\']([^"\']+)["\']',
        r'heartbeat_id\s*=\s*["\']([^"\']+)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, msg)
        if match:
            return match.group(1)
    return ""


async def _post_heartbeat_once(clob, heartbeat_id: str) -> tuple[str, float]:
    """Post one heartbeat and return the latest heartbeat id plus latency."""
    loop = asyncio.get_running_loop()
    t0 = time.time()
    result = await loop.run_in_executor(None, clob.post_heartbeat, heartbeat_id)
    latency_ms = (time.time() - t0) * 1000
    return _extract_heartbeat_id(result) or heartbeat_id, latency_ms


async def _heartbeat_loop(
    clob,
    interval: float = 5.0,
    liveness_state: "LivenessState | None" = None,
) -> None:
    """Send POST /heartbeat to CLOB every interval seconds (live mode only).

    Polymarket's heartbeat protocol is session-based. The first request uses
    an empty id, then every request sends the most recent heartbeat_id returned
    by the server. If an id expires, the API returns 400 with the correct id;
    update to that id and retry immediately.

    When `liveness_state` is supplied, success/failure timestamps and the
    consecutive-failure counter are written to it for the LivenessMonitor and
    /health endpoint to observe.
    """
    heartbeat_id = ""
    log.info("heartbeat loop started (interval=%.0fs)", interval)
    next_tick = time.monotonic()

    def _mark_success() -> None:
        if liveness_state is not None:
            liveness_state.last_heartbeat_ok_ts = time.time()
            liveness_state.last_heartbeat_attempt_ts = time.time()
            liveness_state.consecutive_heartbeat_failures = 0

    def _mark_failure() -> None:
        if liveness_state is not None:
            liveness_state.last_heartbeat_attempt_ts = time.time()
            liveness_state.consecutive_heartbeat_failures += 1

    while True:
        try:
            heartbeat_id, latency_ms = await _post_heartbeat_once(clob, heartbeat_id)
            log.info(f"heartbeat OK ({latency_ms:.0f}ms) id={heartbeat_id[:8]}...")
            _mark_success()
        except Exception as exc:
            corrected_id = _extract_heartbeat_id(exc)
            if corrected_id:
                heartbeat_id = corrected_id
                log.info(f"heartbeat id refreshed from server, id={heartbeat_id[:8]}...")
                try:
                    heartbeat_id, latency_ms = await _post_heartbeat_once(
                        clob, heartbeat_id
                    )
                    log.info(
                        f"heartbeat retry OK ({latency_ms:.0f}ms) "
                        f"id={heartbeat_id[:8]}..."
                    )
                    _mark_success()
                except Exception as retry_exc:
                    log.warning(
                        f"heartbeat retry failed (orders may auto-cancel): {retry_exc}"
                    )
                    _mark_failure()
            else:
                log.warning(f"heartbeat failed (orders may auto-cancel): {exc}")
                _mark_failure()

        next_tick += interval
        await asyncio.sleep(max(0.0, next_tick - time.monotonic()))


async def _resolution_sweep_loop(
    app_state: AppState,
    maker_state: MakerState,
    cancel_q: asyncio.Queue,
    interval: float = 300.0,
) -> None:
    """Book resolved markets the bot still holds inventory in.

    A position in a resolved (untradeable) market can never close on its own:
    it stays in inventory forever, consuming the inventory budget and the
    active-market set. This loop detects resolution, books the terminal P&L
    via MakerState.book_resolution, and cancels any resting quotes.

    A market is treated as resolved only when its price is terminal
    (>= 0.95 YES / <= 0.05 NO) AND either its end date has passed or the price
    is decisive (>= 0.99 / <= 0.01) — this avoids booking an intraday spike on
    a market that is still genuinely open.
    """
    import httpx
    from datetime import datetime

    while True:
        await asyncio.sleep(interval)
        async with maker_state._lock:
            held = {t: inv for t, inv in maker_state.inventory.items() if inv != 0.0}
        if not held:
            continue
        async with app_state._lock:
            end_iso = {
                t: (getattr(app_state.markets.get(t), "end_date_iso", "") or "")
                for t in held
            }
        now = time.time()
        for token_id, inv in held.items():
            end_passed = False
            iso = end_iso.get(token_id, "")
            if iso:
                try:
                    end_ts = datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
                    end_passed = now >= end_ts
                except ValueError:
                    pass
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        "https://clob.polymarket.com/last-trade-price",
                        params={"token_id": token_id},
                    )
                price = float(resp.json()["price"]) if resp.status_code == 200 else None
            except Exception as exc:
                log.debug(f"resolution sweep [{token_id[:8]}]: price fetch failed: {exc}")
                continue
            if price is None:
                continue
            decisive = price >= 0.99 or price <= 0.01
            if price >= 0.95 and (end_passed or decisive):
                resolved_yes = True
            elif price <= 0.05 and (end_passed or decisive):
                resolved_yes = False
            else:
                continue  # still open, or terminal price not yet trustworthy
            async with maker_state._lock:
                realized = maker_state.book_resolution(token_id, resolved_yes)
            await cancel_q.put(CancelAll(token_id))
            log.warning(
                f"RESOLUTION [{token_id[:12]}] resolved "
                f"{'YES' if resolved_yes else 'NO'} (price={price:.3f}) — "
                f"booked inv={inv:+.1f}sh realized={realized:+.2f}"
            )


async def _reconcile_positions_once(
    app_state: AppState,
    maker_state: MakerState,
    wallet: str,
    settle_delay: float = 10.0,
) -> None:
    """One-shot startup reconciliation of maker inventory against real
    on-chain Polymarket holdings.

    The checkpoint and fill-ledger replay can desync from reality — manual
    trades, hand-edited state, partial gap-replay. This fetches the wallet's
    actual positions and overrides the reconstructed inventory so the bot
    never quotes or drains a position that does not exist on-chain.

    Runs once: at startup there are no in-flight fills, so the data-API has
    no lag to fight. Mid-session manual trades are handled by restarting.
    """
    if not wallet:
        return
    from collections import defaultdict
    from trading.redeemall import fetch_positions, extract_wallet_token_id

    await asyncio.sleep(settle_delay)  # let CLOBMonitor seed app_state.markets
    for _ in range(20):
        async with app_state._lock:
            seeded = len(app_state.markets)
        if seeded:
            break
        await asyncio.sleep(2.0)

    try:
        positions = await fetch_positions(wallet)
    except Exception as exc:
        log.warning(f"position reconcile: data-API fetch failed — {exc}")
        return

    # Map every YES and NO token to (yes_token_id, sign). Bot inventory is
    # signed-YES: +1 long YES, -1 holding NO (== short YES).
    async with app_state._lock:
        tok_map: dict[str, tuple[str, float]] = {}
        mids: dict[str, float] = {}
        for yes_id, cs in app_state.markets.items():
            tok_map[yes_id] = (yes_id, 1.0)
            mids[yes_id] = cs.mid
            if cs.no_token_id:
                tok_map[cs.no_token_id] = (yes_id, -1.0)

    onchain: dict[str, float] = defaultdict(float)
    unmapped = 0
    for pos in positions:
        token = extract_wallet_token_id(pos)
        size = float(pos.get("size", 0.0) or 0.0)
        if size == 0.0:
            continue
        mapped = tok_map.get(token)
        if mapped is None:
            unmapped += 1
            continue
        yes_id, sign = mapped
        onchain[yes_id] += sign * size

    async with maker_state._lock:
        bot_nonzero = {t for t, v in maker_state.inventory.items() if v != 0.0}
        corrected = 0
        for tid in bot_nonzero | set(onchain):
            bot_qty = maker_state.inventory.get(tid, 0.0)
            true_qty = round(onchain.get(tid, 0.0), 2)
            if abs(bot_qty - true_qty) < 0.01:
                continue
            corrected += 1
            log.warning(
                f"RECONCILE [{tid[:12]}]: bot inventory {bot_qty:+.2f} "
                f"-> on-chain {true_qty:+.2f}"
            )
            if true_qty == 0.0:
                maker_state.inventory.pop(tid, None)
                maker_state._open_lots.pop(tid, None)
                maker_state.inventory_entry_time.pop(tid, None)
                maker_state.reduce_only_markets.discard(tid)
                maker_state.cooldowns.pop(tid, None)
            else:
                # Synthetic single lot — the true entry price is unknown after
                # a desync, so cost basis is approximate (current mid). Side
                # matches the bot's signed-YES lot convention.
                maker_state.inventory[tid] = true_qty
                price = mids.get(tid, 0.5) or 0.5
                side = "BUY" if true_qty > 0 else "SELL"
                maker_state._open_lots[tid] = [[side, round(price, 4), abs(true_qty)]]
                maker_state.inventory_entry_time[tid] = time.time()
                maker_state.reduce_only_markets.add(tid)

    if corrected:
        log.warning(
            f"position reconcile: corrected {corrected} market(s) "
            f"against on-chain holdings"
        )
    else:
        log.info("position reconcile: maker inventory matches on-chain holdings")
    if unmapped:
        log.info(
            f"position reconcile: {unmapped} on-chain position(s) not in "
            f"tracked markets — skipped"
        )


async def _periodic_data_api_sync(
    app_state: AppState,
    maker_state: MakerState,
    wallet: str,
    interval: float = 300.0,
) -> None:
    """Periodic loop to synchronize local state with Polymarket Data API ground truth.

    Ensures the bot's inventory and P&L (Cash & MTM) never drift from the
    official dashboard values. Automatically purges inventory for resolved
    markets that the local state might have missed.
    """
    if not wallet:
        return

    from trading.redeemall import fetch_positions, extract_wallet_token_id
    import httpx

    while True:
        await asyncio.sleep(interval)
        try:
            positions = await fetch_positions(wallet)
            
            # Map every YES and NO token to (yes_token_id, sign).
            async with app_state._lock:
                tok_map: dict[str, tuple[str, float]] = {}
                for yes_id, cs in app_state.markets.items():
                    tok_map[yes_id] = (yes_id, 1.0)
                    if cs.no_token_id:
                        tok_map[cs.no_token_id] = (yes_id, -1.0)

            onchain_inventory: dict[str, float] = {}
            total_cash_pnl = 0.0
            total_current_value = 0.0

            for pos in positions:
                token = extract_wallet_token_id(pos)
                size = float(pos.get("size", 0.0) or 0.0)
                cash_pnl = float(pos.get("cashPnl", 0.0) or 0.0)
                cur_val = float(pos.get("currentValue", 0.0) or 0.0)
                
                total_cash_pnl += cash_pnl
                total_current_value += cur_val

                if size > 0:
                    mapped = tok_map.get(token)
                    if mapped:
                        yes_id, sign = mapped
                        onchain_inventory[yes_id] = onchain_inventory.get(yes_id, 0.0) + (sign * size)

            async with maker_state._lock:
                # Update P&L ground truth
                maker_state.official_cash_pnl = round(total_cash_pnl, 4)
                maker_state.official_mtm_pnl = round(total_cash_pnl + total_current_value, 4)
                
                # Update inventory ground truth
                # Purge local inventory if not found on-chain (resolved or manual exit)
                for tid in list(maker_state.inventory.keys()):
                    if maker_state.inventory[tid] != 0.0 and tid not in onchain_inventory:
                        log.warning(f"SYNC PURGE: [{tid[:8]}] not in official positions — zeroing inventory")
                        maker_state.inventory[tid] = 0.0
                        maker_state.inventory_entry_time.pop(tid, None)
                        maker_state.reduce_only_markets.discard(tid)

                # Update sizes for active positions
                for tid, true_qty in onchain_inventory.items():
                    bot_qty = maker_state.inventory.get(tid, 0.0)
                    if abs(bot_qty - true_qty) > 0.01:
                        log.info(f"SYNC RECONCILE: [{tid[:8]}] {bot_qty:+.1f} -> {true_qty:+.1f}sh")
                        maker_state.inventory[tid] = true_qty

            log.debug(f"Data API sync complete: MTM P&L=${maker_state.official_mtm_pnl:+.2f}")

        except Exception as exc:
            log.warning(f"Periodic Data API sync failed: {exc}")


def build_maker_actors(
    app_state: AppState,
    paper: bool = True,
    shadow: bool = False,
    clob=None,
    bankroll: float = 500.0,
    fill_ledger: FillLedger | None = None,
    maker_state: MakerState | None = None,
) -> tuple[dict, dict]:
    """Create all actors and queues. Returns (actors_dict, queues_dict)."""
    if maker_state is None:
        maker_state = MakerState()

    # Queues
    active_markets_q = asyncio.Queue()
    quote_intents_q = asyncio.Queue()
    fills_q = asyncio.Queue()
    skew_updates_q = asyncio.Queue()
    cancel_q = asyncio.Queue()
    price_update_q = asyncio.Queue(maxsize=200)  # CLOBMonitor → QuoteEngine price ticks
    markout_q: asyncio.Queue = asyncio.Queue()    # InventoryManager → MarkoutTracker
    # Shadow mode: CLOBMonitor routes real trade events here instead of Poisson dice
    trades_q: asyncio.Queue | None = asyncio.Queue(maxsize=500) if shadow else None

    queues = {
        "active_markets_q": active_markets_q,
        "quote_intents_q": quote_intents_q,
        "fills_q": fills_q,
        "skew_updates_q": skew_updates_q,
        "cancel_q": cancel_q,
        "price_update_q": price_update_q,
        "markout_q": markout_q,
        "trades_q": trades_q,
    }

    # Shadow mode swaps the paper Poisson fill model for real-trade-driven fills.
    # OrderManager still uses paper=True (no real orders placed).
    if shadow:
        fill_poller = ShadowFillPoller(app_state, maker_state, fills_q, trades_q)
    else:
        fill_poller = FillPoller(app_state, maker_state, fills_q, clob=clob, paper=paper)

    markout_tracker = MarkoutTracker(
        app_state, maker_state, markout_q,
        kill_switch_enabled=(not paper and not shadow),
    )

    actors = {
        "selector": MarketSelector(app_state, active_markets_q, maker_state=maker_state),
        "quote_engine": QuoteEngine(
            app_state, maker_state, active_markets_q, quote_intents_q, skew_updates_q,
            price_update_q=price_update_q,
            cancel_q=cancel_q,
            markout_tracker=markout_tracker,
        ),
        "order_manager": OrderManager(
            maker_state, clob=clob,
            paper=True if shadow else paper,  # shadow always paper; live respects config
            quote_intents_q=quote_intents_q, cancel_q=cancel_q,
            app_state=app_state,
        ),
        "fill_poller": fill_poller,
        "inventory": InventoryManager(
            maker_state, fills_q, skew_updates_q, cancel_q,
            bankroll=bankroll, app_state=app_state, markout_q=markout_q,
            fill_ledger=fill_ledger,
        ),
        "markout_tracker": markout_tracker,
    }

    return actors, queues


async def run_maker():
    """Main async entrypoint for maker mode."""
    from feeds.microstructure import MicrostructureFeed
    from feeds.category_priors import CategoryPriorFeed
    from market.clob_monitor import CLOBMonitor
    from trading.balance import BalancePoller
    from dashboard.server import start_dashboard_server
    from dashboard.state import DashboardState
    from maker.dashboard_state import MakerDashboardState
    from maker.dashboard_loop import maker_dashboard_loop

    paper = config.PAPER
    shadow = config.SHADOW and paper  # shadow requires paper mode
    if config.SHADOW and not paper:
        log.warning("SHADOW=true requires PAPER=true — shadow mode disabled")
    if paper and not shadow:
        log.warning(
            "Running Poisson paper mode — fills are synthetic, not from real trades. "
            "This is a plumbing smoke test only. Set SHADOW=true (now the default) "
            "for realistic fill validation."
        )

    log.info(f"Starting maker bot (paper={paper}, shadow={shadow})")

    app_state = AppState()

    # Set up maker dashboard
    maker_dash = MakerDashboardState()
    maker_dash.update({"paper": paper, "shadow": shadow})
    dash = DashboardState()
    dashboard_port = int(os.getenv("DASHBOARD_PORT", "5050"))
    start_dashboard_server(dash, port=dashboard_port, maker_dash=maker_dash)
    log.info(f"Maker dashboard at http://127.0.0.1:{dashboard_port}/maker")

    # Build CLOB client for live mode
    clob = None
    wallet_address = ""
    if not paper:
        from trading.executor import CLOBExecutor
        executor = CLOBExecutor(paper=False)
        clob = executor._clob
        wallet_address = executor.wallet_address

    # Persistence: fill ledger + state loader + checkpointer
    fill_ledger = FillLedger(base_dir="maker_data")
    lifetime_cache = LifetimeStatsCache(base_dir="maker_data")
    preloaded_state = MakerState()
    MakerStateLoader(preloaded_state, fill_ledger, paper=paper).load()

    # Per-category inventory caps. config.MAKER_CATEGORY_CAPS honours the
    # per-category env vars (MAKER_WEATHER_INV_CAP, MAKER_SPORTS_INV_CAP, ...);
    # categories without an explicit override use MAKER_NON_MODEL_INV_CAP.
    non_model_cap = float(os.getenv("MAKER_NON_MODEL_INV_CAP", "12"))
    preloaded_state.category_inventory_caps = {
        cat: config.MAKER_CATEGORY_CAPS.get(cat, non_model_cap)
        for cat in ("weather", "sports", "event", "election",
                    "finance", "macro", "rates", "crypto", "unknown")
    }

    actors, queues = build_maker_actors(
        app_state=app_state,
        paper=paper,
        shadow=shadow,
        clob=clob,
        bankroll=config.BANKROLL_USDC,
        fill_ledger=fill_ledger,
        maker_state=preloaded_state,
    )

    # Expose MakerState for dashboard loop via order_manager
    maker_state_ref = actors["order_manager"]._maker

    # Checkpointer — saves state every 60s, detects midnight to update lifetime cache
    checkpointer = MakerCheckpointer(
        maker_state_ref,
        fill_ledger=fill_ledger,
        lifetime_cache=lifetime_cache,
    )

    from feeds.falcon import FalconFeed

    # CLOBMonitor gets trades_q in shadow mode so ShadowFillPoller receives real trade events
    clob_monitor = CLOBMonitor(
        app_state,
        price_update_q=queues["price_update_q"],
        trades_q=queues["trades_q"],
    )

    coros = [
        clob_monitor.start(),
        MicrostructureFeed(app_state).start(),
        CategoryPriorFeed(app_state).start(),
        FalconFeed(app_state).start(),
        checkpointer.checkpoint_loop(),
    ]

    if config.OVERNIGHT_VIABILITY_ENABLED:
        coros.append(OvernightViabilityMonitor(maker_dash).run())
        log.info(
            "Overnight viability monitor enabled: "
            f"hours>={config.OVERNIGHT_VIABILITY_MIN_HOURS}, "
            f"fills>={config.OVERNIGHT_VIABILITY_MIN_SESSION_FILLS}, "
            f"realized_pnl>={config.OVERNIGHT_VIABILITY_MIN_SESSION_REALIZED_PNL}, "
            f"markout30>={config.OVERNIGHT_VIABILITY_MIN_SESSION_MARKOUT_30S}, "
            f"rebate_eligible_active>={config.OVERNIGHT_VIABILITY_MIN_REBATE_ELIGIBLE_ACTIVE}"
        )

    if wallet_address:
        coros.append(BalancePoller(app_state, wallet_address, clob=clob).start())
        # One-shot startup reconciliation against real on-chain holdings —
        # corrects any checkpoint/ledger desync before it can be quoted.
        coros.append(_reconcile_positions_once(
            app_state, maker_state_ref, wallet_address,
        ))
        # Periodic sync loop: ensures P&L and inventory stay accurate over time.
        coros.append(_periodic_data_api_sync(
            app_state, maker_state_ref, wallet_address,
        ))

    if not paper:
        coros.append(_balance_guard_loop(
            app_state,
            maker_state_ref,
            queues["cancel_q"],
            min_balance=config.MAKER_MIN_BALANCE_USDC,
        ))

    # Liveness state shared by heartbeat loop, LivenessMonitor, and /health endpoint.
    liveness_state = LivenessState()
    sinks = [FileSink(), StdoutSink()]
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        sinks.append(TelegramSink())
        log.info("liveness: TelegramSink enabled")
    dispatcher = AlertDispatcher(sinks)

    if clob is not None:
        coros.append(_heartbeat_loop(
            clob,
            interval=config.MAKER_HEARTBEAT_INTERVAL_SECONDS,
            liveness_state=liveness_state,
        ))
        coros.append(LivenessMonitor(
            clob, maker_state_ref, liveness_state, dispatcher, interval=30.0,
        ).run())

    # Resolution sweep — books resolved markets the bot still holds inventory
    # in, so a resolved position cannot stay stuck in the inventory budget.
    coros.append(_resolution_sweep_loop(
        app_state, maker_state_ref, queues["cancel_q"],
    ))

    # Redemption loop — recycles resolved positions every 15 min (no-op in paper mode)
    coros.append(
        redeemall_loop(
            paper=paper,
            wallet=wallet_address,
            rpc_url=config.RPC_URL,
            private_key=config.POLY_PRIVATE_KEY if not paper else "",
        )
    )

    # Maker actors
    for actor in actors.values():
        coros.append(actor.run())

    # Expired-position cleanup: zeroes stale paper inventory every 5 min.
    # Must run as a separate coroutine so it fires even when the fills queue
    # is idle.  Delayed 60 s to let CLOBMonitor seed app_state.markets first.
    coros.append(actors["inventory"].cleanup_loop())

    # Dashboard snapshot loop — pass markout_tracker for live-validation stats
    coros.append(
        maker_dashboard_loop(
            app_state, maker_state_ref, maker_dash,
            markout_tracker=actors["markout_tracker"],
        )
    )

    vpin_poller = VPINPoller(app_state)
    coros.append(_run_vpin_poller(vpin_poller))

    log.info(f"Maker bot running with {len(actors)} actors")
    try:
        await asyncio.gather(*coros)
    finally:
        # Shutdown: save final checkpoint and close fill ledger
        log.info("Maker shutting down — saving final checkpoint")
        checkpointer.save()
        fill_ledger.close()
