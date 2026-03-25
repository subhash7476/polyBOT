"""
main.py — Polymarket Bot v2.1 Orchestrator

Wires all feeds + trading loop. PAPER=true by default.
Set PAPER=false in .env only after the paper validation checklist passes (see README).

v2.1 fixes applied:
  Fix 1: BUY_YES/BUY_NO direction via get_trade_direction
  Fix 2: engine returned from build_model_probability, wired to passes_signal_filter
  Fix 3: _contract_group_key() used in RiskManager (done in risk.py)
  Fix 4: spread_penalty + adverse_selection in calculate_ev (done in ev_gate.py)
  Fix 5: CachedValue + confidence fields in MacroFeed + FeedState (done)
"""

import asyncio
import os
from pathlib import Path
import config
from config import BANKROLL_USDC, SIGNAL_WEIGHTS, PAPER
from market.state import AppState
from feeds.deribit import DeribitFeed
from feeds.microstructure import MicrostructureFeed
from feeds.onchain import OnChainFeed
from feeds.macro import MacroFeed
from market.clob_monitor import CLOBMonitor, build_threshold_markets
from engine.arb_scanner import find_monotonicity_violations
from engine.probability import build_model_probability
from engine.macro_probability import build_macro_probability
from engine.contract_parser import parse_contract
from engine.signal_filter import passes_signal_filter
from trading.ev_gate import calculate_ev, should_enter, get_trade_direction
from trading.kelly import fractional_kelly
from trading.slippage import estimate_slippage
from trading.risk import RiskManager
from trading.executor import CLOBExecutor
from trading.redeemall import run_redeemall
from trading.balance import BalancePoller
from calibration.tracker import CalibrationTracker
from dashboard.state import DashboardState
from dashboard.loops import dashboard_loop, update_scan_stats
from dashboard.server import start_dashboard_server
from utils.logger import get_logger

log = get_logger("main")

_SCAN_INTERVAL = 5   # seconds between opportunity scans
_ARB_INTERVAL = 30   # seconds between arb scans


async def trading_loop(
    state: AppState,
    risk: RiskManager,
    executor: CLOBExecutor,
    tracker: CalibrationTracker,
    dash: DashboardState,
):
    while True:
        await asyncio.sleep(_SCAN_INTERVAL)
        async with state._lock:
            markets = dict(state.markets)
            feeds = state.feeds

        if not feeds.is_fresh():
            log.debug("feeds stale — skipping scan")
            continue

        # In paper mode, expire stale positions so the bot keeps exploring
        if PAPER:
            expired = risk.expire_paper_positions(config.PAPER_POSITION_TTL_HOURS)
            if expired:
                log.info(f"expired {expired} paper position(s) — slots reopened")

        n_total = len(markets)
        n_parseable = n_signal = n_liquidity = n_ev = n_traded = 0

        for yes_token_id, contract_state in markets.items():
            try:
                # 1. Parse contract — skip if unparseable
                parsed = parse_contract(yes_token_id, contract_state.question)
                if not parsed.parseable:
                    continue
                n_parseable += 1

                # Dashboard record — filled in as we progress through gates
                mkt_rec = {
                    "question": contract_state.question,
                    "model_prob": None,
                    "market_mid": contract_state.mid,
                    "ev": None,
                    "signal_count": 0,
                    "traded": False,
                    "reason": "signal filter",
                }

                # 2. Build probability — dispatch by category
                if parsed.category in ("macro", "rates"):
                    model_prob, signal_count, engine = build_macro_probability(
                        parsed, feeds, SIGNAL_WEIGHTS
                    )
                else:
                    model_prob, signal_count, engine = build_model_probability(
                        parsed, feeds, SIGNAL_WEIGHTS
                    )
                mkt_rec["model_prob"] = model_prob
                mkt_rec["signal_count"] = signal_count

                # 3. Signal agreement filter — Fix 2: properly wired
                ok, reason = passes_signal_filter(engine)
                if not ok:
                    log.debug(f"signal filter: {reason}")
                    mkt_rec["reason"] = reason
                    dash.update({"active_markets_append": mkt_rec})
                    continue
                n_signal += 1
                mkt_rec["reason"] = "illiquid"

                # 4. Determine direction, then estimate slippage on the correct token
                direction, _ = get_trade_direction(model_prob, contract_state.mid)
                if direction == "BUY_YES":
                    slip_bid, slip_ask = contract_state.best_bid, contract_state.best_ask
                else:
                    # BUY_NO: NO ask = 1 - YES bid, NO bid = 1 - YES ask
                    slip_bid = 1.0 - contract_state.best_ask
                    slip_ask = 1.0 - contract_state.best_bid

                slippage = estimate_slippage(
                    side="BUY",
                    size_usdc=50.0,  # pre-Kelly estimate
                    best_bid=slip_bid,
                    best_ask=slip_ask,
                    volume_usd=contract_state.volume_usd,
                )
                if not slippage.tradeable:
                    log.info(
                        f"illiquid [{yes_token_id[:8]}]: bid={slip_bid:.3f} ask={slip_ask:.3f} "
                        f"spread={slip_ask-slip_bid:.3f} vol=${contract_state.volume_usd:.0f}"
                    )
                    dash.update({"active_markets_append": mkt_rec})
                    continue
                n_liquidity += 1
                mkt_rec["reason"] = "low EV"

                # 5. EV gate
                ev, side = calculate_ev(
                    model_prob=model_prob,
                    market_price=contract_state.mid,
                    slippage=slippage,
                    ev_multiplier=risk.ev_multiplier,
                )
                mkt_rec["ev"] = ev
                enter, enter_reason = should_enter(ev, slippage, risk.ev_multiplier)
                if not enter:
                    log.info(f"ev gate [{yes_token_id[:8]}]: {enter_reason} model={model_prob:.3f} mid={contract_state.mid:.3f}")
                    mkt_rec["reason"] = enter_reason
                    dash.update({"active_markets_append": mkt_rec})
                    continue
                n_ev += 1
                mkt_rec["reason"] = "risk block"

                # 6. Kelly sizing
                size = fractional_kelly(
                    model_prob=model_prob,
                    market_price=contract_state.mid,
                    bankroll=BANKROLL_USDC,
                    ev=ev,
                    signal_count=signal_count,
                )
                if size <= 0:
                    log.info(f"kelly=0 [{yes_token_id[:8]}]: model={model_prob:.3f} mid={contract_state.mid:.3f}")
                    mkt_rec["reason"] = "kelly=0"
                    dash.update({"active_markets_append": mkt_rec})
                    continue

                # 7. Risk gate — Fix 3: direction-bucketed group check
                ok, risk_reason = await risk.can_trade(yes_token_id, parsed, size, feeds)
                if not ok:
                    log.info(f"risk block [{yes_token_id[:8]}]: {risk_reason}")
                    mkt_rec["reason"] = risk_reason
                    dash.update({"active_markets_append": mkt_rec})
                    continue

                # 8. Log signal (before execution)
                tracker.log_signal(
                    token_id=yes_token_id,
                    model_prob=model_prob,
                    market_prob=contract_state.mid,
                    signal_summary=engine.summary(),
                    size_usdc=size,
                    ev=ev,
                    side=side,
                )

                # 9. Execute
                log.info(
                    f"{'[PAPER] ' if PAPER else ''}{side} {yes_token_id[:8]} "
                    f"size=${size:.2f} model={model_prob:.3f} "
                    f"market={contract_state.mid:.3f} ev={ev:.3f}"
                )
                result = await executor.place_order(
                    yes_token_id=yes_token_id,
                    no_token_id=contract_state.no_token_id,
                    side=side,
                    size=size,
                    price=contract_state.best_ask if side == "BUY_YES" else contract_state.no_best_ask,
                )
                if result.success:
                    await risk.open_position(yes_token_id, parsed, size, result.filled_price)
                    n_traded += 1
                    mkt_rec["traded"] = True
                    mkt_rec["reason"] = None
                dash.update({"active_markets_append": mkt_rec})

            except Exception as exc:
                log.exception(f"trading loop error for {yes_token_id[:8]}: {exc}")

        log.info(
            f"scan: {n_total} markets | {n_parseable} parseable | "
            f"{n_signal} signal ok | {n_liquidity} liquid | {n_ev} ev+"
        )
        update_scan_stats(dash, n_total=n_total, n_parseable=n_parseable,
                          n_signal=n_signal, n_liquidity=n_liquidity,
                          n_ev=n_ev, n_traded=n_traded)


async def arb_scan_loop(
    state: AppState,
    tracker: CalibrationTracker,
):
    """Scan for cross-market monotonicity violations every 30s."""
    while True:
        await asyncio.sleep(_ARB_INTERVAL)
        async with state._lock:
            markets = dict(state.markets)

        threshold_markets = build_threshold_markets(markets)
        violations = find_monotonicity_violations(threshold_markets, min_spread=0.03)

        for v in violations:
            log.info(f"ARB: {v.trade_description} | spread={v.spread:.3f}")
            tracker.log_signal(
                token_id=f"arb_{v.low_strike_token[:8]}_{v.high_strike_token[:8]}",
                model_prob=0.99,
                market_prob=0.50,
                signal_summary={"type": "arb", "spread": v.spread},
                size_usdc=0.0,
                ev=v.spread,
                side="ARB",
            )

        if violations:
            log.info(f"arb scan: {len(violations)} violations from {len(threshold_markets)} threshold markets")


async def redeemall_loop(executor, interval: int = 900):
    """Check for redeemable positions every 15 minutes."""
    await asyncio.sleep(60)  # wait 60s before first check
    while True:
        try:
            await run_redeemall(
                wallet=executor.wallet_address,
                rpc_url=config.RPC_URL,
                private_key=config.POLY_PRIVATE_KEY,
            )
        except Exception as exc:
            log.error(f"redeemall_loop error: {exc}")
        await asyncio.sleep(interval)


async def main():
    Path(__file__).parent.joinpath("bot.pid").write_text(str(os.getpid()))
    log.info(f"starting v2.1 | paper={PAPER} | bankroll=${BANKROLL_USDC}")
    state = AppState()
    risk = RiskManager(bankroll=BANKROLL_USDC)
    executor = CLOBExecutor(paper=PAPER)
    tracker = CalibrationTracker()
    dash = DashboardState()
    start_dashboard_server(dash, port=5050)
    balance_poller = BalancePoller(state, wallet_address=executor.wallet_address)

    await asyncio.gather(
        DeribitFeed(state).start(),
        MicrostructureFeed(state).start(),
        OnChainFeed(state).start(),
        MacroFeed(state).start(),
        CLOBMonitor(state).start(),
        trading_loop(state, risk, executor, tracker, dash),
        arb_scan_loop(state, tracker),
        dashboard_loop(state, dash, risk=risk),
        balance_poller.start(),
        redeemall_loop(executor),
    )


if __name__ == "__main__":
    asyncio.run(main())
