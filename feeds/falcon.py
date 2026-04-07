"""FalconFeed — polls the Falcon API for whale intelligence and market insights.

All requests go through a single parameterised endpoint:
  POST https://narrative.agent.heisenberg.so/api/v2/semantic/retrieve/parameterized

Dataset routing via agent_id:
  584 — Falcon Leaderboard  (15-day filtered smart-money list)
  581 — Wallet 360          (deep per-wallet analytics)
  575 — Market Insights     (volume trends + whale-control flags per market)

Credentials: set FALCON_API_KEY in .env — never hardcode.
"""

import asyncio
import time
import httpx
import config
from market.state import AppState, FalconWalletStats, FalconMarketInsight
from utils.logger import get_logger

log = get_logger(__name__)

_ENDPOINT = config.FALCON_BASE_URL   # full URL to the parameterised endpoint


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {config.FALCON_API_KEY}",
        "Content-Type": "application/json",
    }


def _post(payload: dict) -> dict:
    """Synchronous helper used only in the async context via httpx.AsyncClient."""
    raise NotImplementedError("use _apost instead")


class FalconFeed:
    """Three independent polling loops writing to AppState.feeds."""

    def __init__(self, state: AppState):
        self._state = state

    async def start(self) -> None:
        if not config.FALCON_API_KEY:
            log.warning("FALCON_API_KEY not set — FalconFeed disabled")
            return

        log.info("FalconFeed starting")
        async with httpx.AsyncClient(timeout=20.0) as client:
            await asyncio.gather(
                self._leaderboard_loop(client),
                self._wallet_loop(client),
                self._market_insights_loop(client),
            )

    # ── Leaderboard loop — discovers smart-money wallets ─────────────────────

    async def _leaderboard_loop(self, client: httpx.AsyncClient) -> None:
        """Poll Falcon Leaderboard (agent 584) to auto-discover elite wallets."""
        while True:
            try:
                wallets = await self._fetch_leaderboard(client)
                if wallets:
                    async with self._state._lock:
                        for w in wallets:
                            self._state.feeds.falcon_whale_stats[w.wallet] = w
                    self._state.stamp_feed("falcon_leaderboard")
                    log.info(f"leaderboard refreshed: {len(wallets)} elite wallets")
            except Exception as exc:
                log.warning(f"leaderboard fetch failed: {exc}")
            await asyncio.sleep(config.FALCON_LEADERBOARD_POLL_INTERVAL)

    async def _fetch_leaderboard(
        self, client: httpx.AsyncClient
    ) -> list[FalconWalletStats]:
        payload = {
            "agent_id": config.FALCON_AGENT_LEADERBOARD,
            "params": {
                "min_win_rate_15d": str(config.FALCON_MIN_WIN_RATE),
                "min_pnl_15d":      str(config.FALCON_MIN_PNL_15D),
                "min_total_trades_15d": str(config.FALCON_MIN_TRADES_15D),
                "sort_by": "h_score",
            },
            "pagination": {"limit": 50, "offset": 0},
            "formatter_config": {"format_type": "raw"},
        }
        resp = await client.post(_ENDPOINT, json=payload, headers=_auth_headers())
        resp.raise_for_status()
        results = resp.json().get("data", {}).get("results", [])

        now = time.time()
        wallets = []
        for r in results:
            wallet = r.get("wallet", "")
            if not wallet:
                continue
            wallets.append(FalconWalletStats(
                wallet=wallet,
                total_pnl=float(r.get("total_pnl_15d", 0.0)),
                roi=float(r.get("roi_pct_15d", 0.0)) / 100.0,       # "18.1" → 0.181
                win_rate=float(r.get("win_rate_pct_15d", 50.0)) / 100.0,  # "57.5" → 0.575
                sharpe_ratio=float(r.get("sharpe_ratio_15d") or 0.0),
                total_trades=int(r.get("total_trades_15d") or 0),
                markets_traded=int(r.get("markets_traded_15d") or 0),
                h_score=float(r.get("h_score", 0.0)),
                trajectory=str(r.get("trajectory", "")),
                leaderboard_rank=int(r.get("leaderboard_rank", 0)),
                fetched_at=now,
            ))
        return wallets

    # ── Wallet 360 loop — deep analytics for each discovered wallet ───────────

    async def _wallet_loop(self, client: httpx.AsyncClient) -> None:
        """Enrich each known wallet with Wallet 360 analytics (agent 581)."""
        while True:
            async with self._state._lock:
                wallets = list(self._state.feeds.falcon_whale_stats.keys())

            for wallet in wallets:
                try:
                    stats = await self._fetch_wallet_360(client, wallet)
                    if stats:
                        async with self._state._lock:
                            # Merge: preserve leaderboard fields, update analytics fields
                            existing = self._state.feeds.falcon_whale_stats.get(wallet)
                            if existing:
                                stats.h_score = existing.h_score
                                stats.trajectory = existing.trajectory
                                stats.leaderboard_rank = existing.leaderboard_rank
                            self._state.feeds.falcon_whale_stats[wallet] = stats
                except Exception as exc:
                    log.debug(f"wallet_360 fetch failed for {wallet[:10]}: {exc}")
                await asyncio.sleep(0.5)

            if wallets:
                self._state.stamp_feed("falcon_wallet360")
                log.info(f"wallet 360 refreshed for {len(wallets)} wallets")

            await asyncio.sleep(config.FALCON_WALLET_POLL_INTERVAL)

    async def _fetch_wallet_360(
        self, client: httpx.AsyncClient, wallet: str
    ) -> FalconWalletStats | None:
        payload = {
            "agent_id": config.FALCON_AGENT_WALLET_360,
            "params": {
                "proxy_wallet": wallet,
                "window_days": "7",
            },
            "pagination": {"limit": 10, "offset": 0},  # min limit enforced server-side
            "formatter_config": {"format_type": "raw"},
        }
        resp = await client.post(_ENDPOINT, json=payload, headers=_auth_headers())
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        results = resp.json().get("data", {}).get("results", [])
        if not results:
            return None
        # Multiple date-snapshots returned — take the most recent
        results.sort(key=lambda x: x.get("date", ""), reverse=True)
        r = results[0]

        return FalconWalletStats(
            wallet=wallet,
            total_pnl=float(r.get("total_pnl", 0.0)),
            roi=float(r.get("roi", 0.0)) / 100.0,   # API returns %; store as fraction
            win_rate=float(r.get("win_rate", 0.5)),  # already a fraction (0.55 = 55%)
            sharpe_ratio=float(r.get("sharpe_ratio", 0.0)),
            max_drawdown=float(r.get("max_drawdown", 0.0)),  # positive abs value from API
            total_trades=int(r.get("total_trades", 0)),
            markets_traded=int(r.get("markets_traded", 0)),
            combined_risk_score=float(r.get("combined_risk_score", 0.0)),
            risk_level=str(r.get("risk_level", "unknown")).lower().capitalize(),
            sybil_risk_flag=bool(r.get("sybil_risk_flag", False)),
            performance_trend=str(r.get("performance_trend", "unknown")),
            edge_decay=float(r.get("edge_decay", 0.0)),
            fetched_at=time.time(),
        )

    # ── Market insights loop — active markets + whale-control flags ───────────

    async def _market_insights_loop(self, client: httpx.AsyncClient) -> None:
        """Poll Market Insights (agent 575) for active market intelligence."""
        while True:
            try:
                insights = await self._fetch_market_insights(client)
                if insights:
                    async with self._state._lock:
                        for ins in insights:
                            self._state.feeds.falcon_market_insights[ins.condition_id] = ins
                            if ins.question:
                                self._state.feeds.falcon_insights_by_question[ins.question.strip().lower()] = ins
                    self._state.stamp_feed("falcon_insights")
                    log.info(f"market insights refreshed: {len(insights)} markets")
            except Exception as exc:
                log.warning(f"market insights fetch failed: {exc}")
            await asyncio.sleep(config.FALCON_MARKET_INSIGHTS_POLL_INTERVAL)

    async def _fetch_market_insights(
        self, client: httpx.AsyncClient
    ) -> list[FalconMarketInsight]:
        payload = {
            "agent_id": config.FALCON_AGENT_MARKET_INSIGHTS,
            "params": {
                "condition_id": "ALL",
                "min_volume_24h": "0",
                "volume_trend": "ALL",
            },
            "pagination": {"limit": 200, "offset": 0},
            "formatter_config": {"format_type": "raw"},
        }
        resp = await client.post(_ENDPOINT, json=payload, headers=_auth_headers())
        resp.raise_for_status()
        results = resp.json().get("data", {}).get("results", [])

        now = time.time()
        insights = []
        for r in results:
            cid = r.get("condition_id", "")
            if not cid:
                continue
            insights.append(FalconMarketInsight(
                condition_id=cid,
                question=str(r.get("question", "")),
                slug=str(r.get("slug", "")),
                end_date=str(r.get("end_date", "")),
                current_volume_24h=float(r.get("current_volume_24h", 0.0)),
                current_volume_7d=float(r.get("current_volume_7d", 0.0)),
                volume_trend=str(r.get("volume_trend", "Normal")),
                liquidity_tier=str(r.get("liquidity_tier", "Low")),
                liquidity_percentile=float(r.get("liquidity_percentile", 0.0)),
                top1_wallet_pct=float(r.get("top1_wallet_pct", 0.0)),
                top3_wallet_pct=float(r.get("top3_wallet_pct", 0.0)),
                whale_control_flag=bool(r.get("whale_control_flag", False)),
                unique_traders_7d=int(r.get("unique_traders_7d", 0)),
                trades_per_hour_avg=float(r.get("trades_per_hour_avg", 0.0)),
                trade_concentration_flag=bool(r.get("trade_concentration_flag", False)),
                fetched_at=now,
            ))
        return insights
