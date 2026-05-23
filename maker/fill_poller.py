"""FillPoller actor — detects order fills at 500ms cadence."""

import asyncio
import math
import random
import time
from maker.state import MakerState
from maker.types import Fill
from market.state import AppState, ContractState
from utils.logger import get_logger

# Assumed competition on each side (other makers at same level).
# Higher = fewer fills per unit of volume.
_COMPETITION_FACTOR = 10.0

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
        self._order_states: dict[str, str] = {}
        # Persistent NO→YES mapping: accumulates across market-selector cycles so
        # resolution sells on deselected markets are still captured.
        self._known_no_to_yes: dict[str, str] = {}

    @staticmethod
    def check_paper_fills(
        maker_state: MakerState,
        markets: dict[str, ContractState],
        poll_interval: float = 0.5,
    ) -> list[Fill]:
        """Simulate fills for paper quotes using a volume-based Poisson model.

        As a maker we post INSIDE the market spread (bid < market_ask, ask > market_bid).
        A taker preferring our price fills us; we model that as a Poisson process:
            rate = market_volume_usd_per_day / (86400 * quote_size * COMPETITION_FACTOR)
        Only the competitive side of each quote is eligible:
            bid eligible: our bid > market best_bid  (we're the best bid)
            ask eligible: our ask < market best_ask  (we're the best ask)
        """
        fills = []
        now = time.time()

        for token_id, levels in list(maker_state.live_orders.items()):
            cs = markets.get(token_id)
            if not cs or cs.volume_usd <= 0:
                continue

            for level in levels:
                bid_price = level.get("bid_price", 0.0)
                ask_price = level.get("ask_price", 1.0)
                bid_size = level.get("bid_size", 0.0)
                ask_size = level.get("ask_size", 0.0)
                bid_order_id = level.get("bid_order_id", "")
                ask_order_id = level.get("ask_order_id", "")

                # Poisson fill probability for this poll window
                size_ref = max(bid_size, ask_size, 1.0)
                rate_per_sec = cs.volume_usd / (86400.0 * size_ref * _COMPETITION_FACTOR)
                p_fill = 1.0 - math.exp(-rate_per_sec * poll_interval)

                mid = cs.mid

                # Bid fills when our bid is at or better than the market's best bid.
                # >= (not >) so that joining the queue (posting exactly at best_bid)
                # also generates paper fills — this is valid for book-relative quotes
                # that compress to the market spread on tight markets.
                if (
                    bid_order_id
                    and bid_size > 0
                    and bid_price >= cs.best_bid
                    and bid_price > 0
                    and random.random() < p_fill
                ):
                    fills.append(Fill(
                        token_id=token_id,
                        side="BUY",
                        price=bid_price,
                        size=bid_size,
                        order_id=bid_order_id,
                        filled_at=now,
                        mid_at_fill=mid,
                    ))

                # Ask fills when our ask is at or better than the market's best ask.
                # <= (not <) to match the bid-side change above.
                if (
                    ask_order_id
                    and ask_size > 0
                    and ask_price <= cs.best_ask
                    and ask_price < 1.0
                    and random.random() < p_fill
                ):
                    fills.append(Fill(
                        token_id=token_id,
                        side="SELL",
                        price=ask_price,
                        size=ask_size,
                        order_id=ask_order_id,
                        filled_at=now,
                        mid_at_fill=mid,
                    ))

        return fills

    @staticmethod
    def consume_paper_fills(maker_state: MakerState, fills: list[Fill]) -> None:
        """Remove filled paper order sides so one synthetic order cannot fill twice."""
        for fill in fills:
            levels = maker_state.live_orders.get(fill.token_id, [])
            updated_levels = []
            for level in levels:
                if fill.side == "BUY" and level.get("bid_order_id", "") == fill.order_id:
                    level = dict(level)
                    level["bid_order_id"] = ""
                    level["bid_size"] = 0.0
                elif fill.side == "SELL" and level.get("ask_order_id", "") == fill.order_id:
                    level = dict(level)
                    level["ask_order_id"] = ""
                    level["ask_size"] = 0.0

                has_bid = bool(level.get("bid_order_id", "")) and level.get("bid_size", 0.0) > 0
                has_ask = bool(level.get("ask_order_id", "")) and level.get("ask_size", 0.0) > 0
                if has_bid or has_ask:
                    updated_levels.append(level)

            if updated_levels:
                maker_state.live_orders[fill.token_id] = updated_levels
            else:
                maker_state.live_orders.pop(fill.token_id, None)

    async def _run_paper(self):
        while True:
            async with self._app._lock:
                markets = dict(self._app.markets)

            fills = self.check_paper_fills(self._maker, markets, poll_interval=self.POLL_INTERVAL)
            self.consume_paper_fills(self._maker, fills)
            for fill in fills:
                log.info(
                    f"PAPER FILL: {fill.side} {fill.size:.2f} @ {fill.price:.3f} "
                    f"[{fill.token_id[:8]}]"
                )
                await self._fills_q.put(fill)

            await asyncio.sleep(self.POLL_INTERVAL)

    async def _run_live(self):
        """Detect live fills via the public data-api activity endpoint.

        L2 auth (get_open_orders / get_trades) is unavailable when
        create_or_derive_api_key fails.  The data-api activity endpoint is
        public (no auth) and returns all trades for a wallet address,
        sufficient for fill detection and inventory reconciliation.
        """
        import os
        import httpx

        funder_address = os.getenv("FUNDER_ADDRESS", "")
        if not funder_address:
            try:
                from eth_account import Account
                pk = os.getenv("POLY_PRIVATE_KEY", "")
                if pk:
                    funder_address = Account.from_key(pk).address
            except Exception:
                pass
        if not funder_address:
            log.error("FillPoller: cannot determine wallet address — fill detection disabled")
            return

        DATA_API = (
            f"https://data-api.polymarket.com/activity"
            f"?user={funder_address}&limit=50"
        )
        log.info(f"FillPoller: live mode — polling data-api for {funder_address[:10]}...")

        # Wait for state_persistence to replay JSONL fills into daily_fills_seen
        # so the startup dedup pass can skip already-recorded fills.
        await asyncio.sleep(10.0)

        seen_tx: set[str] = set()
        POLL_SECS = 5.0

        async with httpx.AsyncClient(timeout=12.0) as client:
            # ── Startup dedup pass ─────────────────────────────────────────
            # Any fill whose fill_id is already in daily_fills_seen was loaded
            # from the JSONL on startup — mark its tx as seen so we don't
            # double-count it.  Fills NOT in daily_fills_seen are processed now
            # so inventory is correct even if this poller missed a prior session.
            try:
                sr = await client.get(DATA_API)
                if sr.status_code == 200:
                    startup_trades = sr.json()
                    if isinstance(startup_trades, list):
                        async with self._app._lock:
                            snap = dict(self._app.markets)
                        no_to_yes_snap = {
                            cs.no_token_id: tid
                            for tid, cs in snap.items()
                            if cs.no_token_id
                        }
                        # Seed persistent cache with what we know now
                        self._known_no_to_yes.update(no_to_yes_snap)
                        for trade in startup_trades:
                            if trade.get("type") != "TRADE":
                                continue
                            tx = trade.get("transactionHash", "")
                            if not tx:
                                continue
                            asset = trade.get("asset", "")
                            raw_price = float(trade.get("price", 0))
                            trade_ts = float(trade.get("timestamp", time.time()))
                            if asset in snap:
                                yes_tid = asset
                                f_side = trade.get("side", "BUY")
                                f_price = raw_price
                            elif asset in self._known_no_to_yes:
                                yes_tid = self._known_no_to_yes[asset]
                                no_side = trade.get("side", "BUY")
                                f_side = "SELL" if no_side == "BUY" else "BUY"
                                f_price = round(1.0 - raw_price, 6)
                            else:
                                seen_tx.add(tx)
                                continue
                            ts_int = int(trade_ts * 1000)
                            fill_id = f"{yes_tid[:8]}-{f_side.lower()}-{f_price:.4f}-{ts_int}"
                            if fill_id in self._maker.daily_fills_seen:
                                seen_tx.add(tx)  # already in JSONL, skip
            except Exception as exc:
                log.warning(f"FillPoller startup dedup error: {exc}")

            # ── Main polling loop ──────────────────────────────────────────
            while True:
                try:
                    resp = await client.get(DATA_API)
                    if resp.status_code != 200:
                        log.warning(f"FillPoller: data-api HTTP {resp.status_code}")
                        await asyncio.sleep(POLL_SECS)
                        continue

                    trades = resp.json()
                    if not isinstance(trades, list):
                        await asyncio.sleep(POLL_SECS)
                        continue

                    async with self._app._lock:
                        markets = dict(self._app.markets)

                    # Merge current markets into persistent NO→YES cache.
                    # This ensures resolution sells on deselected markets are still
                    # captured even after the market falls out of the active set.
                    cur_no_to_yes = {
                        cs.no_token_id: tid
                        for tid, cs in markets.items()
                        if cs.no_token_id
                    }
                    self._known_no_to_yes.update(cur_no_to_yes)

                    for trade in trades:
                        if trade.get("type") != "TRADE":
                            continue
                        tx = trade.get("transactionHash", "")
                        if not tx or tx in seen_tx:
                            continue
                        seen_tx.add(tx)

                        asset = trade.get("asset", "")
                        raw_price = float(trade.get("price", 0))
                        size = float(trade.get("size", 0))
                        trade_ts = float(trade.get("timestamp", time.time()))

                        # Map to YES-space. For NO-token events:
                        #   BUY  NO @ p → SELL YES @ (1-p), cash = -(p × size)  [spent]
                        #   SELL NO @ p → BUY  YES @ (1-p), cash = +(p × size)  [received]
                        actual_cash_flow = None
                        if asset in markets:
                            yes_token_id = asset
                            fill_side = trade.get("side", "BUY")
                            fill_price = raw_price
                        elif asset in self._known_no_to_yes:
                            yes_token_id = self._known_no_to_yes[asset]
                            no_side = trade.get("side", "BUY")
                            fill_side = "SELL" if no_side == "BUY" else "BUY"
                            fill_price = round(1.0 - raw_price, 6)
                            # Sign based on whether we spent or received USDC on the NO side
                            actual_cash_flow = -(raw_price * size) if no_side == "BUY" else +(raw_price * size)
                        else:
                            log.debug(f"FillPoller: unknown asset {asset[:16]} — skip")
                            continue

                        cs = markets.get(yes_token_id)
                        mid = cs.mid if cs else fill_price

                        fill = Fill(
                            token_id=yes_token_id,
                            side=fill_side,
                            price=fill_price,
                            size=size,
                            order_id=tx,
                            filled_at=trade_ts,
                            mid_at_fill=mid,
                            actual_cash_flow=actual_cash_flow,
                        )
                        log.info(
                            f"FILL: {fill.side} {fill.size:.2f}sh @ {fill.price:.4f} "
                            f"[{yes_token_id[:12]}] tx={tx[:14]}"
                        )
                        await self._fills_q.put(fill)

                    if len(seen_tx) > 10_000:
                        seen_tx.clear()

                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning(f"FillPoller._run_live error: {exc}")

                await asyncio.sleep(POLL_SECS)

    async def run(self):
        if self._paper:
            await self._run_paper()
        else:
            await self._run_live()
