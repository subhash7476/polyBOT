"""MarketSelector actor — picks which markets to quote."""

import asyncio
import json
import os
import httpx
import time
from collections import OrderedDict
from datetime import datetime, timezone
from market.state import AppState, ContractState, FeedState
from market.clob_monitor import fetch_active_markets
from utils.logger import get_logger

log = get_logger(__name__)

_MIN_DAILY_VOLUME = 3_000.0     # widened from 10k to sample broader universe during live-validation
_MAX_ACTIVE_MARKETS = 30        # focus on best opportunities, not thin spread
_MIN_SPREAD = 0.01              # widened from 2¢ to 1¢; QuoteEngine.MIN_SPREAD=0.02 still protects edge
_MIN_BID = 0.10      # exclude near-zero / near-resolved markets (bid < 10¢)
_MAX_BID = 0.90      # exclude near-certain markets (bid > 90¢)
_MAX_DAYS_TO_RESOLVE = float(os.getenv("MAKER_MAX_DAYS_TO_RESOLVE", "7"))   # near-expiry only
_MIN_DAYS_TO_RESOLVE = float(os.getenv("MAKER_MIN_DAYS_TO_RESOLVE", "0.17"))  # ≥4h — skip imminent resolution
_REQUIRE_END_DATE = os.getenv("MAKER_REQUIRE_END_DATE", "true").lower() == "true"

# Categories that are excluded from maker quoting regardless of spread/volume.
# Maker bot is category-agnostic — election, sports, event, politics,
# entertainment, technology are all fair game if the spread/volume is there.
# "unknown" (parse_contract miss) is now allowed: empirically ~35% of state-filter
# rejects, includes high-volume geopolitical/policy markets we want to quote.
_EXCLUDED_CATEGORIES = frozenset()

# Falcon insight freshness gate (3× poll interval = 6 min at default 120s)
_FALCON_STALE_SECONDS = 360.0

# Volume trend multipliers applied to base score
_TREND_MULT = {
    "Spiking":        1.5,   # active flow — prioritise
    "Normal":         1.0,
    "Declining":      0.7,
    "Dying Interest": 0.2,   # near-dead market — strongly deprioritise
    "No Trades":      0.05,  # no activity — virtually exclude
}


def _get_falcon_insight(
    condition_id: str,
    question: str,
    feeds: FeedState,
) -> "FalconMarketInsight | None":
    """Return the Falcon insight for a market, trying condition_id first then question text.

    Falcon's condition_id and Gamma's conditionId are often different identifiers for
    the same market.  Question text is a reliable fallback since both sources use the
    same Polymarket question strings.
    """
    ins = feeds.falcon_market_insights.get(condition_id)
    if ins is None and question:
        ins = feeds.falcon_insights_by_question.get(question.strip().lower())
    return ins


def _falcon_score(condition_id: str, feeds: FeedState | None, question: str = "") -> tuple[float, str]:
    """
    Returns (multiplier, reason) applied to the base spread×volume score.
    1.0 = neutral / no data.   > 1.0 = Falcon-preferred.   < 1.0 = deprioritised.

    Rules (cumulative):
      whale_control_flag=True  → ×0.3  (informed flow — adverse selection risk)
      volume_trend              → ×_TREND_MULT[trend]
      unique_traders_7d > 1000 → ×1.3  (diverse flow — lower adverse selection)
      unique_traders_7d < 10   → ×0.5  (illiquid — harder to get fills)
    """
    if feeds is None:
        return 1.0, "no_feeds"

    insight = _get_falcon_insight(condition_id, question, feeds)
    if insight is None:
        return 1.0, "no_data"

    if (time.time() - insight.fetched_at) > _FALCON_STALE_SECONDS:
        return 1.0, "stale"

    mult = 1.0
    tags = []

    if insight.whale_control_flag:
        mult *= 0.3
        tags.append(f"whale({insight.top1_wallet_pct:.0f}%)")

    trend_mult = _TREND_MULT.get(insight.volume_trend, 1.0)
    if trend_mult != 1.0:
        mult *= trend_mult
        tags.append(insight.volume_trend.lower().replace(" ", "_"))

    if insight.unique_traders_7d > 1000:
        mult *= 1.3
        tags.append("diverse_flow")
    elif insight.unique_traders_7d < 10:
        mult *= 0.5
        tags.append("illiquid")

    reason = ",".join(tags) if tags else "normal"
    return mult, reason


def _falcon_overlap_report(
    selected: "OrderedDict[str, ContractState]",
    feeds: FeedState | None,
) -> None:
    """Log which selected markets appear in Falcon's market insights and what they show."""
    if feeds is None or not feeds.falcon_market_insights:
        log.info("Falcon overlap: no market insights data available yet")
        return

    n_covered = 0
    n_spiking = 0
    n_whale = 0
    covered_lines: list[str] = []

    for token_id, cs in selected.items():
        insight = _get_falcon_insight(cs.condition_id, cs.question, feeds)
        if insight is None:
            continue
        n_covered += 1
        trend = insight.volume_trend
        whale = "🐋 " if insight.whale_control_flag else ""
        if trend == "Spiking":
            n_spiking += 1
        if insight.whale_control_flag:
            n_whale += 1
        covered_lines.append(
            f"  {whale}{cs.question[:40]:<40} trend={trend:<14} "
            f"top1={insight.top1_wallet_pct:.0f}% traders={insight.unique_traders_7d}"
        )

    log.info(
        f"Falcon overlap: {n_covered}/{len(selected)} selected markets have Falcon data "
        f"({n_spiking} spiking, {n_whale} whale-controlled)"
    )
    for line in covered_lines[:15]:
        log.info(line)

    # Report Falcon top markets NOT in our selection (missed opportunities)
    # Match by both condition_id AND question text (IDs may differ across sources)
    selected_cids = {cs.condition_id for cs in selected.values()}
    selected_questions = {cs.question.strip().lower() for cs in selected.values()}
    falcon_not_selected = [
        ins for ins in feeds.falcon_market_insights.values()
        if ins.condition_id not in selected_cids
        and ins.question.strip().lower() not in selected_questions
        and ins.volume_trend == "Spiking"
        and not ins.whale_control_flag
        and (time.time() - ins.fetched_at) < _FALCON_STALE_SECONDS
    ]
    falcon_not_selected.sort(key=lambda x: -x.current_volume_24h)
    if falcon_not_selected:
        log.info(
            f"Falcon spiking markets NOT in selection ({len(falcon_not_selected)} total, "
            f"top 5 by 24h volume):"
        )
        # Build a question→(token_id, cs) index for reverse lookup
        state_by_question = {
            cs.question.strip().lower(): (tid, cs)
            for tid, cs in selected.items()
        }
        # Also build full-state question index (not just selected) for diagnosis
        for ins in falcon_not_selected[:5]:
            q_key = ins.question.strip().lower()
            in_selected = q_key in state_by_question
            # Check if the question appears in falcon_insights_by_question
            q_indexed = q_key in (feeds.falcon_insights_by_question if feeds else {})
            log.info(
                f"  {ins.question[:50]:<50} vol24h=${ins.current_volume_24h:,.0f} "
                f"traders={ins.unique_traders_7d} "
                f"in_selected={in_selected} q_indexed={q_indexed}"
            )


class MarketSelector:
    """Selects quotable markets ranked by spread × volume × Falcon score.

    Category-agnostic: any market (election, sports, event, politics, technology,
    entertainment …) is eligible if it passes the spread/volume/bid-range filters.
    Only 'unknown' category markets are excluded (contract parser couldn't classify them).
    """

    REFRESH_INTERVAL = 900  # 15 minutes

    def __init__(
        self,
        state: AppState,
        active_markets_q: asyncio.Queue,
        maker_state=None,
    ):
        self._state = state
        self._active_markets_q = active_markets_q
        self._maker_state = maker_state

    @staticmethod
    def filter_and_rank(
        markets: dict[str, ContractState],
        max_markets: int = _MAX_ACTIVE_MARKETS,
        feeds: FeedState | None = None,
    ) -> "OrderedDict[str, ContractState]":
        """Filter to quotable markets, rank by spread × volume × falcon_score."""
        candidates = []
        falcon_log: list[str] = []

        # Diagnostic counters
        n_excluded_cat = n_low_vol = n_bad_bid = n_far_future = n_tight_spread = n_too_soon = n_no_date = 0
        cat_counts: dict[str, int] = {}
        now_ts = time.time()

        # One-time diagnostic: show Falcon spiking questions vs state market questions
        if feeds and feeds.falcon_market_insights:
            spiking_insights = [
                ins for ins in feeds.falcon_market_insights.values()
                if ins.volume_trend == "Spiking"
            ][:3]
            if spiking_insights:
                log.info("DIAG Falcon spiking questions (first 3):")
                for ins in spiking_insights:
                    log.info(f"  cid={ins.condition_id[:12]} q={repr(ins.question[:80])}")
                state_questions = list(markets.values())[:3]
                log.info("DIAG State market questions (first 3):")
                for cs in state_questions:
                    log.info(f"  cid={cs.condition_id[:12]} q={repr(cs.question[:80])}")

        for token_id, cs in markets.items():
            cat_counts[cs.category] = cat_counts.get(cs.category, 0) + 1
            if cs.category in _EXCLUDED_CATEGORIES:
                n_excluded_cat += 1
                continue

            spread = cs.best_ask - cs.best_bid
            if spread < _MIN_SPREAD:
                n_tight_spread += 1
                continue

            vol_check = cs.volume_24h if cs.volume_24h > 0 else cs.volume_usd
            if vol_check < _MIN_DAILY_VOLUME:
                n_low_vol += 1
                continue
            if cs.best_bid < _MIN_BID or cs.best_bid > _MAX_BID:
                n_bad_bid += 1
                continue

            # Resolve-date guard: exclude markets with no known resolution date.
            # Empty end_date_iso = Gamma didn't provide one and contract_parser
            # couldn't infer one (common for long-dated election markets).
            # Treating them as quotable creates 7-month inventory traps.
            if not cs.end_date_iso:
                if _REQUIRE_END_DATE:
                    n_no_date += 1
                    continue

            # Near-expiry filter: only quote markets resolving within the window.
            # Too far out = frozen price, inventory trap.
            # Too close = resolution imminent, quotes are dangerous.
            if cs.end_date_iso:
                try:
                    end_dt = datetime.fromisoformat(cs.end_date_iso.replace("Z", "+00:00"))
                    days_left = (end_dt.timestamp() - now_ts) / 86400.0
                    if days_left > _MAX_DAYS_TO_RESOLVE:
                        n_far_future += 1
                        continue
                    if days_left < _MIN_DAYS_TO_RESOLVE:
                        n_too_soon += 1
                        continue
                except ValueError:
                    pass

            # Rank by 24h volume if available — reflects current taker activity
            vol_rank = cs.volume_24h if cs.volume_24h > 0 else cs.volume_usd
            base_score = spread * vol_rank

            # #6 Fee multiplier: fee-free markets (negRisk weather) attract takers
            # with no crossing cost → higher fill probability for the same spread.
            fee_mult = 1.2 if not cs.fees_enabled else 1.0

            # #2 Depth multiplier: thin books mean we're the primary liquidity
            # provider → lower queue competition → higher fill probability.
            # bid_depth/ask_depth = sum of best-5 level sizes (shares).
            total_depth = cs.bid_depth + cs.ask_depth
            if total_depth < 10.0:
                depth_mult = 1.3   # empty house — we set the price
            elif total_depth < 50.0:
                depth_mult = 1.1   # thin book
            else:
                depth_mult = 1.0   # deep book — competing against many makers

            falcon_mult, reason = _falcon_score(cs.condition_id, feeds, question=cs.question)
            score = base_score * fee_mult * depth_mult * falcon_mult

            mults = []
            if fee_mult != 1.0:
                mults.append(f"fee_free×{fee_mult:.1f}")
            if depth_mult != 1.0:
                mults.append(f"thin_book×{depth_mult:.1f}")
            if falcon_mult != 1.0:
                mults.append(f"falcon×{falcon_mult:.2f}({reason})")
            if mults:
                falcon_log.append(f"{cs.question[:35]}… {' '.join(mults)}")

            candidates.append((token_id, cs, score))

        candidates.sort(key=lambda x: -x[2])

        log.info(
            f"filter_and_rank: {len(markets)} markets in state "
            f"(cats={cat_counts}) → "
            f"excluded_cat={n_excluded_cat} low_vol={n_low_vol} "
            f"tight_spread={n_tight_spread} bad_bid={n_bad_bid} "
            f"no_date={n_no_date} far_future={n_far_future} too_soon={n_too_soon} → "
            f"{len(candidates)} candidates → {min(len(candidates), max_markets)} selected"
        )

        # Diagnose top Falcon spiking markets: why aren't they in state / candidates?
        if feeds and feeds.falcon_market_insights:
            spiking_top = sorted(
                [ins for ins in feeds.falcon_market_insights.values()
                 if ins.volume_trend == "Spiking" and not ins.whale_control_flag],
                key=lambda x: -x.current_volume_24h,
            )[:5]
            state_by_question = {cs.question.strip().lower(): cs for cs in markets.values()}
            log.info("DIAG top spiking markets:")
            for ins in spiking_top:
                q_key = ins.question.strip().lower()
                cs = state_by_question.get(q_key)
                if cs is None:
                    status = "NOT_IN_STATE"
                elif cs.category in _EXCLUDED_CATEGORIES:
                    status = f"EXCLUDED_CAT({cs.category})"
                elif (cs.best_ask - cs.best_bid) < _MIN_SPREAD:
                    ins2 = _get_falcon_insight(cs.condition_id, cs.question, feeds)
                    status = f"TIGHT(spread={cs.best_ask-cs.best_bid:.3f}) ins_found={ins2 is not None}"
                elif cs.volume_usd < _MIN_DAILY_VOLUME:
                    status = f"LOW_VOL({cs.volume_usd:.0f})"
                elif cs.best_bid < _MIN_BID or cs.best_bid > _MAX_BID:
                    status = f"BAD_BID({cs.best_bid:.3f})"
                else:
                    status = "PASSED"
                log.info(f"  {ins.question[:55]:<55} → {status}")

        if falcon_log:
            log.info(f"Falcon re-ranking applied to {len(falcon_log)} markets:")
            for entry in falcon_log[:10]:
                log.info(f"  {entry}")

        result: OrderedDict[str, ContractState] = OrderedDict()
        for token_id, cs, _ in candidates[:max_markets]:
            result[token_id] = cs
        return result

    async def _discover_and_seed(self) -> None:
        """
        Fetch all markets from Gamma and seed any quotable ones missing from state.

        CLOBMonitor's 250-slot selection is volume-sorted (taker-optimised), which
        can crowd out wide-spread markets in less-liquid categories. This method
        ensures the maker bot has all candidates regardless of CLOBMonitor ordering.
        Seeds up to 2× _MAX_ACTIVE_MARKETS candidates (sorted by spread×volume).
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                token_map = await fetch_active_markets(client)
        except Exception as exc:
            log.warning(f"MarketSelector Gamma fetch failed: {exc}")
            return

        candidates = []
        n_excluded_cat = n_low_vol = n_bad_bid = n_far_future = n_no_date = 0
        now_ts = time.time()
        for yes_id, meta in token_map.items():
            if meta["category"] in _EXCLUDED_CATEGORIES:
                n_excluded_cat += 1
                continue
            if (meta.get("volume_24h", 0) or meta.get("volume", 0)) < _MIN_DAILY_VOLUME:
                n_low_vol += 1
                continue
            if meta["best_bid"] < _MIN_BID or meta["best_bid"] > _MAX_BID:
                n_bad_bid += 1
                continue
            expiry = meta.get("expiry")
            if expiry is None:
                if _REQUIRE_END_DATE:
                    n_no_date += 1
                    continue
            else:
                exp_ts = expiry.timestamp() if hasattr(expiry, "timestamp") else float(expiry)
                days_left = (exp_ts - now_ts) / 86400.0
                if days_left > _MAX_DAYS_TO_RESOLVE:
                    n_far_future += 1
                    continue
                if days_left < _MIN_DAYS_TO_RESOLVE:
                    continue
            # Spread filter — same as filter_and_rank
            spread = meta["best_ask"] - meta["best_bid"]
            if spread < _MIN_SPREAD:
                continue
            vol_rank = meta.get("volume_24h", 0) or meta.get("volume", 0)
            candidates.append((yes_id, meta, spread * vol_rank))

        log.info(
            f"_discover_and_seed: {len(token_map)} Gamma markets scanned "
            f"(excluded={n_excluded_cat} low_vol={n_low_vol} "
            f"bad_bid={n_bad_bid} no_date={n_no_date} far_future={n_far_future}) "
            f"→ {len(candidates)} candidates"
        )

        candidates.sort(key=lambda x: -x[2])
        top = candidates[:_MAX_ACTIVE_MARKETS * 2]  # seed 2x buffer

        # Force-seed top Falcon spiking markets that fell below the spread×vol
        # ranking window.  These enter state so filter_and_rank() can report their
        # true exclusion reason (tight_spread) instead of NOT_IN_STATE.
        # Includes markets filtered by no-date/far-future guards — intentional,
        # since high-volume spiking markets often lack a fixed end date.
        # Cap at top-20 spiking markets by 24h volume; skip whale-controlled ones.
        async with self._state._lock:
            falcon_insights = dict(self._state.feeds.falcon_market_insights)
        if falcon_insights:
            # Build condition_id → yes_token_id reverse map from full Gamma scan
            cid_to_yes: dict[str, str] = {
                meta["condition_id"]: yes_id
                for yes_id, meta in token_map.items()
                if meta.get("condition_id")
            }
            top_candidate_cids = {meta["condition_id"] for _, meta, _ in top if meta.get("condition_id")}
            spiking = sorted(
                [
                    ins for ins in falcon_insights.values()
                    if ins.volume_trend == "Spiking"
                    and not ins.whale_control_flag
                    and ins.condition_id in cid_to_yes
                    and ins.condition_id not in top_candidate_cids
                    and ins.current_volume_24h >= _MIN_DAILY_VOLUME
                    and (time.time() - ins.fetched_at) < _FALCON_STALE_SECONDS
                ],
                key=lambda x: -x.current_volume_24h,
            )[:20]
            for ins in spiking:
                yes_id = cid_to_yes[ins.condition_id]
                meta = token_map[yes_id]
                bid = meta.get("best_bid", 0.0)
                if bid < _MIN_BID or bid > _MAX_BID:
                    continue
                top.append((yes_id, meta, 0.0))  # score=0 — included for state visibility only

        # Build a token_id → condition_id map from the FULL Gamma scan (all 3670+
        # markets, not just candidates).  Used below to backfill condition_ids on
        # markets already in state that were seeded with an empty condition_id — this
        # is the prerequisite for the Falcon spiking bypass and Falcon scoring to work.
        token_to_cid: dict[str, str] = {
            yes_id: meta["condition_id"]
            for yes_id, meta in token_map.items()
            if meta.get("condition_id")
        }

        seeded = 0
        backfilled_cid = 0
        async with self._state._lock:
            existing = set(self._state.markets.keys())

            # Backfill condition_ids for markets already in state
            for yes_id, cs in self._state.markets.items():
                if not cs.condition_id and yes_id in token_to_cid:
                    cs.condition_id = token_to_cid[yes_id]
                    backfilled_cid += 1

            # Seed new wide-spread markets not yet in state
            for yes_id, meta, _ in top:
                if yes_id in existing:
                    continue
                expiry = meta.get("expiry")
                end_date_iso = expiry.isoformat() if expiry and hasattr(expiry, "isoformat") else ""
                cs = ContractState(
                    yes_token_id=yes_id,
                    no_token_id=meta.get("no_token_id", ""),
                    question=meta["question"],
                    category=meta["category"],
                    best_bid=meta["best_bid"],
                    best_ask=meta["best_ask"],
                    volume_usd=meta.get("volume", 0),
                    condition_id=meta.get("condition_id", ""),
                    end_date_iso=end_date_iso,
                )
                self._state.markets[yes_id] = cs
                seeded += 1

        if seeded or backfilled_cid:
            log.info(
                f"MarketSelector seeded {seeded} markets; "
                f"backfilled condition_ids for {backfilled_cid} existing markets"
            )

    async def _discover_from_falcon_DISABLED(self) -> None:  # kept for reference only
        """
        NOTE: superseded by the token_to_cid backfill in _discover_and_seed.
        Gamma API does not support conditionId as a filter parameter, so per-lookup
        calls return the full market list; this approach was unreliable.

        Seed markets identified by Falcon Market Insights that are not yet in state.

        Falcon's agent 575 returns condition_ids for active markets.  Some of
        these — particularly "Spiking" high-volume markets — may sit beyond
        CLOBMonitor's 250-slot cap or MarketSelector's normal Gamma pages.
        This method resolves those condition_ids via the Gamma API and seeds
        any missing markets into state so they become quotable.

        Only spiking / normal Falcon markets with volume >= _MIN_DAILY_VOLUME
        and bid within [_MIN_BID, _MAX_BID] are seeded.  Whale-controlled and
        stale insights are skipped.
        """
        async with self._state._lock:
            insights = dict(self._state.feeds.falcon_market_insights)
            existing_cids = {
                cs.condition_id
                for cs in self._state.markets.values()
                if cs.condition_id
            }

        to_discover = [
            ins for ins in insights.values()
            if ins.condition_id not in existing_cids
            and ins.volume_trend in ("Spiking", "Normal")
            and not ins.whale_control_flag
            and ins.current_volume_24h >= _MIN_DAILY_VOLUME
            and (time.time() - ins.fetched_at) < _FALCON_STALE_SECONDS
        ]

        if not to_discover:
            return

        # Prioritise spiking markets and those with the highest 24h volume
        to_discover.sort(key=lambda x: (x.volume_trend != "Spiking", -x.current_volume_24h))
        batch = to_discover[:30]  # at most 30 Gamma lookups per cycle

        log.info(f"Falcon discovery: resolving {len(batch)} condition_ids via Gamma API")
        seeded = 0
        backfilled = 0

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                for ins in batch:
                    try:
                        resp = await client.get(
                            "https://gamma-api.polymarket.com/markets",
                            params={
                                "conditionId": ins.condition_id,
                                "active": "true",
                                "closed": "false",
                                "enableOrderBook": "true",
                            },
                        )
                        resp.raise_for_status()
                        raw = resp.json()
                        if isinstance(raw, dict):
                            raw = raw.get("data", [])
                        if not raw:
                            continue

                        m = raw[0]
                        if not m.get("acceptingOrders"):
                            continue

                        token_ids_raw = m.get("clobTokenIds", "[]")
                        try:
                            token_ids = (
                                json.loads(token_ids_raw)
                                if isinstance(token_ids_raw, str)
                                else token_ids_raw
                            )
                        except Exception:
                            continue
                        if len(token_ids) < 2:
                            continue

                        yes_id, no_id = token_ids[0], token_ids[1]

                        # If the token is already in state but lacks a condition_id,
                        # backfill it from the Falcon insight so the spiking bypass
                        # and Falcon scoring work on the next filter_and_rank cycle.
                        async with self._state._lock:
                            existing_cs = self._state.markets.get(yes_id)
                            if existing_cs is not None:
                                if not existing_cs.condition_id and ins.condition_id:
                                    existing_cs.condition_id = ins.condition_id
                                    backfilled += 1
                                    log.debug(
                                        f"Falcon: backfilled condition_id for [{yes_id[:8]}] "
                                        f"({ins.question[:40]})"
                                    )
                                continue  # already in state — no full re-seed needed

                        question = m.get("question", ins.question)
                        raw_bid = float(m.get("bestBid") or 0)
                        raw_ask = float(m.get("bestAsk") or 1)

                        # Synthesise from outcome price if book is empty
                        outcome_prices_raw = m.get("outcomePrices") or []
                        if isinstance(outcome_prices_raw, str):
                            try:
                                outcome_prices_raw = json.loads(outcome_prices_raw)
                            except Exception:
                                outcome_prices_raw = []
                        yes_price = float(outcome_prices_raw[0]) if outcome_prices_raw else None
                        if (raw_ask - raw_bid) > 0.5 and yes_price and 0.03 < yes_price < 0.97:
                            raw_bid = round(yes_price - 0.02, 4)
                            raw_ask = round(yes_price + 0.02, 4)

                        if raw_bid < _MIN_BID or raw_bid > _MAX_BID:
                            continue

                        from engine.contract_parser import parse_contract
                        parsed = parse_contract(yes_id, question)
                        category = parsed.category if parsed.parseable else "event"
                        if category in _EXCLUDED_CATEGORIES:
                            continue

                        volume = float(m.get("volumeClob") or m.get("volume") or ins.current_volume_24h)

                        cs = ContractState(
                            yes_token_id=yes_id,
                            no_token_id=no_id,
                            question=question,
                            category=category,
                            best_bid=raw_bid,
                            best_ask=raw_ask,
                            volume_usd=volume,
                            condition_id=ins.condition_id,
                        )
                        async with self._state._lock:
                            self._state.markets[yes_id] = cs
                        seeded += 1

                    except Exception as exc:
                        log.debug(f"Falcon discovery lookup failed [{ins.condition_id[:10]}]: {exc}")

                    await asyncio.sleep(0.15)  # gentle rate limit

        except Exception as exc:
            log.warning(f"Falcon discovery client error: {exc}")

        if seeded or backfilled:
            log.info(
                f"Falcon discovery: seeded={seeded} new markets, "
                f"backfilled={backfilled} condition_ids on existing markets"
            )

    async def _run_one_cycle(self) -> None:
        """One selection cycle: discover, rank, update state, emit to queue."""
        await self._discover_and_seed()

        async with self._state._lock:
            markets = dict(self._state.markets)
            feeds = self._state.feeds

        selected = self.filter_and_rank(markets, feeds=feeds)
        by_cat: dict[str, int] = {}
        for cs in selected.values():
            by_cat[cs.category] = by_cat.get(cs.category, 0) + 1

        log.info(
            f"MarketSelector: {len(selected)} markets selected "
            f"(from {len(markets)} total) — by_cat={by_cat}"
        )

        _falcon_overlap_report(selected, feeds)

        if self._maker_state is not None:
            async with self._maker_state._lock:
                prev_selected = set(self._maker_state.selected_token_ids)
                self._maker_state.selected_token_ids = set(selected.keys())

            deselected = prev_selected - set(selected.keys())
            async with self._maker_state._lock:
                for token_id in deselected:
                    inv = self._maker_state.get_inventory(token_id)
                    if inv != 0.0:
                        self._maker_state.reduce_only_markets.add(token_id)
                        log.info(
                            f"Orphaned: [{token_id[:8]}] {inv:+.1f}sh — added to reduce_only"
                        )
                closed = {
                    t for t in self._maker_state.reduce_only_markets
                    if self._maker_state.get_inventory(t) == 0.0
                }
                self._maker_state.reduce_only_markets -= closed
                if closed:
                    log.info(f"Closed {len(closed)} orphaned position(s) — removed from reduce_only")

        await self._active_markets_q.put(set(selected.keys()))

    async def run(self):
        """Main loop — re-evaluate market selection every REFRESH_INTERVAL."""
        # Wait for CLOBMonitor to seed initial markets before first selection
        while True:
            async with self._state._lock:
                n = len(self._state.markets)
            if n > 0:
                break
            await asyncio.sleep(2.0)

        while True:
            await self._run_one_cycle()
            await asyncio.sleep(self.REFRESH_INTERVAL)
