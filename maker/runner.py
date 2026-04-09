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
from maker.shadow_fill_poller import ShadowFillPoller
from maker.inventory import InventoryManager
from maker.markout_tracker import MarkoutTracker
from maker.fill_ledger import FillLedger
from maker.state_persistence import MakerCheckpointer, MakerStateLoader
from trading.redeemall import redeemall_loop
from utils.logger import get_logger

log = get_logger(__name__)


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

    actors = {
        "selector": MarketSelector(app_state, active_markets_q),
        "quote_engine": QuoteEngine(
            app_state, maker_state, active_markets_q, quote_intents_q, skew_updates_q,
            price_update_q=price_update_q,
        ),
        "order_manager": OrderManager(
            maker_state, clob=clob,
            paper=True if shadow else paper,  # shadow always paper; live respects config
            quote_intents_q=quote_intents_q, cancel_q=cancel_q,
        ),
        "fill_poller": fill_poller,
        "inventory": InventoryManager(
            maker_state, fills_q, skew_updates_q, cancel_q,
            bankroll=bankroll, app_state=app_state, markout_q=markout_q,
            fill_ledger=fill_ledger,
        ),
        "markout_tracker": MarkoutTracker(app_state, markout_q),
    }

    return actors, queues


async def run_maker():
    """Main async entrypoint for maker mode."""
    from feeds.microstructure import MicrostructureFeed
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
    start_dashboard_server(dash, port=5050, maker_dash=maker_dash)
    log.info("Maker dashboard at http://127.0.0.1:5050/maker")

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
    preloaded_state = MakerState()
    MakerStateLoader(preloaded_state, fill_ledger, paper=paper).load()

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

    # Checkpointer — saves state every 60s
    checkpointer = MakerCheckpointer(maker_state_ref)

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
        FalconFeed(app_state).start(),
        checkpointer.checkpoint_loop(),
    ]

    if wallet_address:
        coros.append(BalancePoller(app_state, wallet_address).start())

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

    # Dashboard snapshot loop — pass markout_tracker for live-validation stats
    coros.append(
        maker_dashboard_loop(
            app_state, maker_state_ref, maker_dash,
            markout_tracker=actors["markout_tracker"],
        )
    )

    log.info(f"Maker bot running with {len(actors)} actors")
    try:
        await asyncio.gather(*coros)
    finally:
        # Shutdown: save final checkpoint and close fill ledger
        log.info("Maker shutting down — saving final checkpoint")
        checkpointer.save()
        fill_ledger.close()
