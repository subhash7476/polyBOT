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
from trading.redeemall import redeemall_loop
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
    from dashboard.server import start_dashboard_server
    from dashboard.state import DashboardState
    from maker.dashboard_state import MakerDashboardState
    from maker.dashboard_loop import maker_dashboard_loop

    paper = config.PAPER
    log.info(f"Starting maker bot (paper={paper})")

    app_state = AppState()

    # Set up maker dashboard
    maker_dash = MakerDashboardState()
    maker_dash.update({"paper": paper})
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

    actors, _ = build_maker_actors(
        app_state=app_state,
        paper=paper,
        clob=clob,
        bankroll=config.BANKROLL_USDC,
    )

    # Expose MakerState for dashboard loop via order_manager
    maker_state_ref = actors["order_manager"]._maker

    coros = [
        # Reused feeds
        CLOBMonitor(app_state).start(),
        MicrostructureFeed(app_state).start(),
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

    # Dashboard snapshot loop
    coros.append(maker_dashboard_loop(app_state, maker_state_ref, maker_dash))

    log.info(f"Maker bot running with {len(actors)} actors")
    await asyncio.gather(*coros)
