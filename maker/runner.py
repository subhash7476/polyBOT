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


async def _heartbeat_loop(clob, interval: float = 30.0) -> None:
    """Send POST /heartbeat to CLOB every interval seconds (live mode only).

    Without this, the CLOB auto-cancels all open orders if the session goes
    quiet (network hiccup, rate-limit pause, etc.), wiping the Liquidity Rewards
    Q-score for those missed minutes.
    """
    while True:
        await asyncio.sleep(interval)
        t0 = time.time()
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None, clob.post_heartbeat
            )
            latency_ms = (time.time() - t0) * 1000
            log.debug(f"heartbeat OK ({latency_ms:.0f}ms): {result}")
        except Exception as exc:
            log.warning(f"heartbeat failed (orders may auto-cancel): {exc}")


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

    if not paper:
        coros.append(_balance_guard_loop(
            app_state,
            maker_state_ref,
            queues["cancel_q"],
            min_balance=config.MAKER_MIN_BALANCE_USDC,
        ))

    if clob is not None:
        coros.append(_heartbeat_loop(clob))

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
