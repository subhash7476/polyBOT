"""QuoteEngine actor — computes fair value and bid/ask quotes."""

import asyncio
import re
import time
from datetime import datetime, timezone
from maker.state import MakerState
from maker.types import QuoteIntent, SkewUpdate, LadderUpdate, CancelAll
from market.state import AppState
from engine.falcon_signals import compute_adverse_selection_penalty, compute_market_skew_adjustment
from engine.contract_parser import parse_contract
from engine.probability import build_model_probability
from engine.weather_probability import build_weather_probability
from engine.macro_probability import build_macro_probability
from engine.orderbook_imbalance import compute_obi_signal
from config import SIGNAL_WEIGHTS
from maker.regime import compute_regime_score, CATEGORY_SPREAD_MULTIPLIER
from utils.logger import get_logger

log = get_logger(__name__)

BASE_SPREAD = 0.06
MIN_SPREAD = 0.02  # tightened from 0.04 to quote inside thinner books
MAX_SPREAD = 0.15
QUOTE_SIZE_USDC = 10.0
SKEW_PER_SHARE = 0.004   # fv shift per share of net position
MAX_SKEW_ABS = 0.10      # hard cap on total inventory adjustment
LADDER_LEVELS = 3    # bid+ask pairs per market
LEVEL_STEP = 0.01    # price offset between ladder levels

# NegRisk weather sibling guard: group buckets by city+date via question-text regex.
# When one bucket moves into resolution territory (mid > NEGRISK_SIBLING_THRESHOLD),
# the remaining buckets will resolve 0 — cancel their quotes immediately.
_WEATHER_GROUP_RE = re.compile(
    r"Will the highest temperature in (.+?) be .+? on (.+?)(?:\?|$)",
    re.IGNORECASE,
)
NEGRISK_SIBLING_THRESHOLD = 0.80


def _negrisk_group_key(question: str) -> "str | None":
    """Return city|date key for NegRisk weather buckets, or None if not parseable."""
    m = _WEATHER_GROUP_RE.match(question.strip())
    if m:
        return f"{m.group(1).strip().lower()}|{m.group(2).strip().rstrip('?').strip().lower()}"
    return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def compute_fair_value(mid: float, inventory: float, model_adj: float) -> float:
    """
    Hybrid fair value: market mid + inventory skew + model adjustment.
    inventory: raw net position (positive = long YES, negative = short YES).
    Long position lowers fv to lean against the book and encourage selling.
    Short position raises fv to encourage buying and close the position.
    model_adj: Falcon/external model adjustment.
    """
    inventory_adj = _clamp(-inventory * SKEW_PER_SHARE, -MAX_SKEW_ABS, MAX_SKEW_ABS)
    return _clamp(mid + inventory_adj + model_adj, 0.05, 0.95)


def compute_spread(
    volume_usd: float,
    abs_inventory: float,
    hours_to_expiry: float,
    dvol: float = 60.0,
) -> float:
    """Dynamic spread based on volume, inventory, time to resolution, and volatility.

    Spread schedule (we only quote 4h–7d markets):
      < 4h : MAX_SPREAD — imminent resolution, stop quoting (MarketSelector excludes these)
      < 12h: +0.06 — very high uncertainty / adverse selection risk
      < 24h: +0.04 — elevated risk
      < 72h: +0.02 — moderate
      3–7d : BASE_SPREAD — normal window
    """
    spread = BASE_SPREAD

    # Volatility scaling (Phase 2)
    # Baseline vol is 60%. If DVOL is 120%, spread doubles (multiplier=2.0).
    # Clamped [1.0, 2.0] to prevent narrowing below BASE_SPREAD heuristic.
    vol_baseline = 60.0
    vol_mult = _clamp(dvol / vol_baseline, 1.0, 2.0)
    spread *= vol_mult

    if volume_usd < 500.0:
        spread += 0.02

    spread += abs_inventory * 0.01

    if hours_to_expiry < 4.0:
        return MAX_SPREAD
    elif hours_to_expiry < 12.0:
        spread += 0.06
    elif hours_to_expiry < 24.0:
        spread += 0.04
    elif hours_to_expiry < 72.0:
        spread += 0.02

    return max(MIN_SPREAD, min(spread, MAX_SPREAD))


class QuoteEngine:
    """Computes quotes for active markets, event-driven on price ticks."""

    FORCE_REPRICE_INTERVAL = 30.0  # fallback: reprice all markets if no tick arrives
    CLOB_STALE_SECONDS = 30.0      # pull all quotes if CLOB feed silent this long

    def __init__(
        self,
        app_state: AppState,
        maker_state: MakerState,
        active_markets_q: asyncio.Queue,
        quote_intents_q: asyncio.Queue,
        skew_updates_q: asyncio.Queue,
        price_update_q: asyncio.Queue | None = None,
        cancel_q: asyncio.Queue | None = None,
        markout_tracker=None,  # MarkoutTracker | None — avoids circular import
    ):
        self._app = app_state
        self._maker = maker_state
        self._active_markets_q = active_markets_q
        self._quote_intents_q = quote_intents_q
        self._skew_updates_q = skew_updates_q
        self._price_update_q = price_update_q
        self._cancel_q = cancel_q
        self._markout_tracker = markout_tracker
        self._clob_stale = False  # tracks whether we already pulled quotes
        self._active_token_ids: set[str] = set()
        self._last_force_reprice: float = 0.0
        self._stale_skip_warned: set[str] = set()   # markets already warned about bid-range exit
        self._pre_res_flatten_warned: set[str] = set()  # markets warned about pre-resolution flatten
        self._last_regime_log: float = 0.0           # throttle regime summary to 1/min

    @staticmethod
    def build_ladder(
        token_id: str,
        fair_value: float,
        spread: float,
        size: float,
        reason: str,
    ) -> LadderUpdate:
        """Build a LADDER_LEVELS-deep ladder centred on fair_value.

        Level layout (LADDER_LEVELS=3, center index=1):
          index 0: bid = fv - half - LEVEL_STEP,  ask = fv + half + LEVEL_STEP
          index 1: bid = fv - half,               ask = fv + half          ← center
          index 2: bid = fv - half + LEVEL_STEP,  ask = fv + half - LEVEL_STEP

        Tightest level (index 2) is closest to mid. Widest (index 0) is outermost.
        All levels carry equal size.
        """
        half = spread / 2.0
        center_idx = LADDER_LEVELS // 2
        levels = []
        for i in range(LADDER_LEVELS):
            offset = (center_idx - i) * LEVEL_STEP
            bid = round(_clamp(fair_value - half - offset, 0.01, 0.99), 4)
            ask = round(_clamp(fair_value + half + offset, 0.01, 0.99), 4)
            if bid >= ask:
                continue  # skip degenerate level (very near 0 or 1)
            levels.append(QuoteIntent(
                token_id=token_id,
                bid_price=bid,
                ask_price=ask,
                bid_size=size,
                ask_size=size,
                reason=reason,
            ))
        return LadderUpdate(token_id=token_id, levels=levels, reason=reason)

    @staticmethod
    def is_stale(old: QuoteIntent, new: QuoteIntent, tick: float = 0.01) -> bool:
        """True if the new quote differs enough from the old to warrant a reprice."""
        return (
            abs(old.bid_price - new.bid_price) >= tick
            or abs(old.ask_price - new.ask_price) >= tick
        )

    async def _reprice(self, tokens_to_check: set[str], force: bool, new_ids: set[str]) -> None:
        """Compute and emit ladder updates for the given token set."""
        async with self._app._lock:
            markets = dict(self._app.markets)
            feeds = self._app.feeds  # reference — Falcon lookups only read, never mutate

        _regime_scores: list[float] = []
        for token_id in tokens_to_check:
            cs = markets.get(token_id)
            if not cs:
                continue

            # Bid-range guard: market has moved out of quotable range since last MarketSelector
            # refresh (every 15 min). Weather uses tighter 0.10/0.90 — NegRisk buckets
            # resolve near 0 once a sibling wins, and resolution-territory spreads are toxic.
            _bid_lo = 0.10 if cs.category == "weather" else 0.05
            _bid_hi = 0.90 if cs.category == "weather" else 0.95
            if (cs.best_bid < _bid_lo or cs.best_bid > _bid_hi
                    or cs.best_ask < _bid_lo or cs.best_ask > _bid_hi):
                if token_id not in self._stale_skip_warned:
                    self._stale_skip_warned.add(token_id)
                    log.warning(
                        f"bid-range guard: [{token_id[:8]}] best_bid={cs.best_bid:.4f} "
                        f"best_ask={cs.best_ask:.4f} out of [{_bid_lo:.2f}, {_bid_hi:.2f}] "
                        f"(cat={cs.category}) — cancelling quotes"
                    )
                if self._cancel_q is not None:
                    await self._cancel_q.put(CancelAll(token_id))
                continue

            # NegRisk sibling guard: if another bucket for the same city+date has moved
            # into resolution territory, the remaining buckets resolve 0 — cancel them.
            if cs.neg_risk and cs.category == "weather":
                group_key = _negrisk_group_key(cs.question)
                if group_key is not None:
                    sibling_resolved = False
                    for sib_id, sib_cs in markets.items():
                        if sib_id == token_id or not sib_cs.neg_risk:
                            continue
                        if (_negrisk_group_key(sib_cs.question) == group_key
                                and sib_cs.mid > NEGRISK_SIBLING_THRESHOLD):
                            sibling_resolved = True
                            if token_id not in self._stale_skip_warned:
                                self._stale_skip_warned.add(token_id)
                                log.warning(
                                    f"negrisk_sibling_guard [{token_id[:8]}]: sibling "
                                    f"[{sib_id[:8]}] at mid={sib_cs.mid:.3f} — cancelling losers"
                                )
                            break
                    if sibling_resolved:
                        if self._cancel_q is not None:
                            await self._cancel_q.put(CancelAll(token_id))
                        continue

            if self._maker.in_cooldown(token_id):
                continue

            # Hard inventory gate: never re-quote a market whose position is already
            # at or above the per-market cap, regardless of cooldown state.
            # Without this gate, cooldown expiry → re-quote → fill → cap fires again
            # → repeat indefinitely, growing positions without bound.
            # Bypass for reduce_only_markets — they need to quote the closing side to drain.
            if (token_id not in self._maker.reduce_only_markets
                    and abs(self._maker.get_inventory(token_id)) >= self._maker.max_inventory_for_category(cs.category)):
                continue

            inv = self._maker.get_inventory(token_id)
            abs_inv = abs(inv)

            # Per-market Falcon adjustments (condition_id matches Market Insights data)
            spread_multiplier = compute_adverse_selection_penalty(cs.condition_id, feeds)
            model_adj = compute_market_skew_adjustment(cs.condition_id, feeds)

            # Compute hours to expiry from market end date
            hours_to_expiry = 999.0
            if cs.end_date_iso:
                try:
                    end_dt = datetime.fromisoformat(cs.end_date_iso.replace("Z", "+00:00"))
                    hours_to_expiry = max(0.0, (end_dt.timestamp() - time.time()) / 3600.0)
                except ValueError:
                    pass

            # Regime score — single call per market per cycle; result reused for all outputs
            _inv_signed = self._maker.skew_factor(token_id, cs.category)
            _markout_30s = (
                self._markout_tracker.get_markout_30s(token_id, cs.category)
                if self._markout_tracker is not None else 0.0
            )
            regime = compute_regime_score(
                vpin=cs.vpin,
                markout_30s=_markout_30s,
                inv_signed=_inv_signed,
                hours_to_resolution=cs.hours_to_resolution,
                category=cs.category,
            )
            log.debug(
                f"regime[{token_id[:8]}] score={regime.score:.3f} "
                f"vpin={cs.vpin:.3f} markout={_markout_30s:.4f} "
                f"inv={_inv_signed:.2f} hours={cs.hours_to_resolution:.1f} "
                f"spread_x={regime.spread_multiplier:.2f} size_x={regime.size_multiplier:.2f} "
                f"skew={regime.skew_adjustment:+.4f} "
                f"flags={','.join(sorted(regime.flags)) or 'none'}"
            )
            _regime_scores.append(regime.score)
            if "extreme" in regime.flags:
                log.warning(f"extreme regime [{token_id[:8]}] score={regime.score:.2f}")
            if "toxic_flow" in regime.flags:
                log.info(f"toxic_flow [{token_id[:8]}] vpin={cs.vpin:.2f}")

            fv = compute_fair_value(mid=cs.mid, inventory=inv, model_adj=model_adj)

            # Phase 1: External Price Discovery (Anchor Model)
            # Phase 2: Volatility-Adaptive Spreading
            # Phase 4: Bayesian Model Mapping (Weather/Macro)
            # Use external feeds (Binance, Deribit, Weather, FRED) to anchor fair value.
            anchor_fv = fv
            asset_dvol = 60.0
            contract = parse_contract(token_id, cs.question)
            if contract.parseable:
                model_prob = None
                sig_count = 0

                if contract.category == "crypto":
                    asset_dvol = feeds.dvol.get(contract.asset, 60.0)
                    model_prob, sig_count, _ = build_model_probability(contract, feeds, SIGNAL_WEIGHTS)
                elif contract.category == "weather":
                    model_prob, sig_count, _ = build_weather_probability(contract, feeds, SIGNAL_WEIGHTS)
                elif contract.category in ("macro", "rates"):
                    model_prob, sig_count, _ = build_macro_probability(contract, feeds, SIGNAL_WEIGHTS)

                if model_prob is not None and sig_count > 0:
                    prob_delta = model_prob - cs.mid
                    # If model deviates from mid, nudge fv toward model (max 3¢)
                    model_anchor_adj = _clamp(prob_delta, -0.03, 0.03)
                    anchor_fv = _clamp(fv + model_anchor_adj, 0.05, 0.95)
                    if abs(model_anchor_adj) >= 0.01:
                        log.debug(
                            f"model_anchor [{token_id[:8]}] cat={contract.category}: "
                            f"mid={cs.mid:.3f} model={model_prob:.3f} "
                            f"adj={model_anchor_adj:+.3f} fv={fv:.3f}→{anchor_fv:.3f}"
                        )

            base_spread = compute_spread(
                volume_usd=cs.volume_usd,
                abs_inventory=abs_inv,
                hours_to_expiry=hours_to_expiry,
                dvol=asset_dvol,
            )

            # Phase 3: Orderbook Imbalance (OBI) Micro-Skew
            # Nudge fair value by up to 1¢ if there's a persistent depth imbalance.
            obi_signal = compute_obi_signal(token_id)
            if obi_signal and abs(obi_signal.strength) > 0.1:
                obi_adj = _clamp(obi_signal.strength * 0.01, -0.01, 0.01)
                anchor_fv = _clamp(anchor_fv + obi_adj, 0.05, 0.95)
                log.debug(
                    f"obi_skew [{token_id[:8]}]: strength={obi_signal.strength:.2f} "
                    f"adj={obi_adj:+.3f} fv→{anchor_fv:.3f}"
                )

            # Apply regime skew to anchor_fv BEFORE spread derivation
            anchor_fv = _clamp(anchor_fv + regime.skew_adjustment, 0.05, 0.95)

            # Category baseline × Falcon adverse-selection × regime dynamic multiplier
            cat_mult = CATEGORY_SPREAD_MULTIPLIER.get(cs.category, 1.5)
            spread = _clamp(
                base_spread * spread_multiplier * cat_mult * regime.spread_multiplier,
                MIN_SPREAD, MAX_SPREAD,
            )

            # Book-relative quoting: compress toward book spread but never below
            # MIN_SPREAD — we need at least that much to capture edge after fees.
            # If the book is tighter than MIN_SPREAD, we quote at MIN_SPREAD
            # (outside the book is fine — we wait for the book to come to us).
            book_spread = cs.best_ask - cs.best_bid
            floor = MIN_SPREAD
            if book_spread > 0 and spread > book_spread:
                eff_spread = max(book_spread, floor)
                eff_half = eff_spread / 2.0
                eff_fv = _clamp(anchor_fv, cs.best_bid + eff_half, cs.best_ask - eff_half)
            else:
                eff_fv = anchor_fv
                eff_spread = spread

            base_size = max(1.0, QUOTE_SIZE_USDC * regime.size_multiplier)
            # Floor size at min_incentive_size so orders qualify for Liquidity Rewards.
            # Cap at 200sh to guard against API outliers.
            if cs.min_incentive_size > 0.0:
                base_size = max(base_size, min(cs.min_incentive_size, 200.0))

            # Warn once per market when all ladder levels are outside the incentive spread
            # window — those orders score 0 for Liquidity Rewards (quadratic penalty).
            if cs.max_incentive_spread > 0.0:
                tightest_half = eff_spread / 2.0 - LEVEL_STEP
                if tightest_half > cs.max_incentive_spread:
                    if token_id not in self._stale_skip_warned:
                        log.warning(
                            f"incentive_spread miss [{token_id[:8]}]: "
                            f"tightest_half={tightest_half:.3f} > max_incentive={cs.max_incentive_spread:.3f} "
                            f"— all ladder levels score 0 for rewards"
                        )

            ladder = self.build_ladder(
                token_id=token_id,
                fair_value=eff_fv,
                spread=eff_spread,
                size=base_size,
                reason="reprice",
            )

            # Pre-resolution flatten guard: at T-2h any meaningful inventory must close.
            # Promote to reduce_only regardless of cap or markout — prevents holding
            # into resolution when there is no time left to recover.
            PRE_RES_HOURS = 2.0
            PRE_RES_INV_THRESHOLD = 0.5
            if (cs.hours_to_resolution <= PRE_RES_HOURS
                    and abs(inv) > PRE_RES_INV_THRESHOLD
                    and token_id not in self._maker.reduce_only_markets):
                self._maker.reduce_only_markets.add(token_id)
                if token_id not in self._pre_res_flatten_warned:
                    self._pre_res_flatten_warned.add(token_id)
                    log.warning(
                        f"PRE-RESOLUTION FLATTEN [{token_id[:8]}]: "
                        f"{cs.hours_to_resolution:.2f}h remaining inv={inv:+.2f} "
                        f"— forcing reduce_only + aggressive_exit"
                    )

            if token_id in self._maker.reduce_only_markets:
                inv = self._maker.get_inventory(token_id)
                if inv != 0.0:
                    # Resolution-territory skip: closing a short at 0.85+ locks in a
                    # large loss with no recovery. Cancel resting quotes and wait —
                    # the market selector will deselect once the MarketSelector next runs,
                    # and the bid-range guard will fire on subsequent reprice ticks.
                    if (inv > 0 and cs.mid < 0.15) or (inv < 0 and cs.mid > 0.85):
                        if self._cancel_q is not None:
                            await self._cancel_q.put(CancelAll(token_id))
                        log.info(
                            f"reduce_only resolution skip [{token_id[:8]}]: "
                            f"inv={inv:+.2f} mid={cs.mid:.3f} — not chasing resolution"
                        )
                        continue

                    # Phase 5: Aggressive Inventory Liquidation
                    # If 30s markout is worse than -10 bps, cross the spread to exit.
                    # This prevents holding toxic inventory in a fast-moving market.
                    m_stats = self._maker.rolling_markouts.get(token_id, {})
                    avg_30s = m_stats.get(30, 0.0)
                    
                    reason = "reduce_only"
                    exit_bid_adj = 0.0
                    exit_ask_adj = 0.0
                    
                    # Aggressive exit when markout is negative OR within 2h of resolution —
                    # at T-2h there is no time to recover, so always cross the spread.
                    if avg_30s < -0.001 or cs.hours_to_resolution <= PRE_RES_HOURS:
                        reason = "aggressive_exit"
                        # Lean 2¢ into the book to ensure immediate fill
                        if inv > 0: # Long YES, need to SELL
                            exit_ask_adj = -0.02
                        else: # Short YES (Long NO), need to BUY
                            exit_bid_adj = 0.02

                    # Cap total closing-side volume to abs(inv) so a simultaneous
                    # sweep of all ladder levels cannot overshoot to the opposite side.
                    remaining_to_close = abs(inv)
                    capped_levels: list[QuoteIntent] = []
                    for l in ladder.levels:
                        if remaining_to_close <= 0.0:
                            break
                        bp = round(_clamp(l.bid_price + exit_bid_adj, 0.01, 0.99), 4)
                        ap = round(_clamp(l.ask_price + exit_ask_adj, 0.01, 0.99), 4)
                        if inv > 0:  # long YES — sell to reduce
                            sz = min(l.ask_size, remaining_to_close)
                            remaining_to_close -= sz
                            capped_levels.append(QuoteIntent(l.token_id, bp, ap, 0.0, sz, reason))
                        else:  # short YES — buy to reduce
                            sz = min(l.bid_size, remaining_to_close)
                            remaining_to_close -= sz
                            capped_levels.append(QuoteIntent(l.token_id, bp, ap, sz, 0.0, reason))
                    new_levels = tuple(capped_levels)
                    ladder = LadderUpdate(token_id, new_levels, reason)

            old_center = self._maker.last_quotes.get(token_id)
            is_new = token_id in new_ids or old_center is None
            if is_new or force or self.is_stale(old_center, ladder.center):
                self._maker.last_quotes[token_id] = ladder.center
                await self._quote_intents_q.put(ladder)
                log.debug(
                    f"ladder [{token_id[:8]}] levels={len(ladder.levels)} "
                    f"center={ladder.center.bid_price:.3f}/{ladder.center.ask_price:.3f} "
                    f"force={force} new={is_new}"
                )

        # Throttled regime summary — visible at INFO level once per minute
        now = time.time()
        if _regime_scores and now - self._last_regime_log >= 60.0:
            avg = sum(_regime_scores) / len(_regime_scores)
            peak = max(_regime_scores)
            log.info(
                f"regime cycle: {len(_regime_scores)} markets "
                f"avg_score={avg:.3f} peak_score={peak:.3f}"
            )
            self._last_regime_log = now

    async def run(self):
        while True:
            # --- Wait for a price tick or force-reprice timeout ---
            triggered_ids: set[str] = set()
            force = False

            if self._price_update_q is not None:
                # Event-driven: block until a tick arrives or 30s elapses
                time_since_force = time.monotonic() - self._last_force_reprice
                timeout = max(0.1, self.FORCE_REPRICE_INTERVAL - time_since_force)
                try:
                    token_id = await asyncio.wait_for(
                        self._price_update_q.get(), timeout=timeout
                    )
                    triggered_ids.add(token_id)
                    # Drain any additional ticks that arrived while we were processing
                    while not self._price_update_q.empty():
                        try:
                            triggered_ids.add(self._price_update_q.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                except asyncio.TimeoutError:
                    force = True  # no tick arrived — force-reprice everything
            else:
                # Fallback polling mode (no price_update_q wired)
                await asyncio.sleep(1.5)

            # --- Drain market selector and skew updates (non-blocking) ---
            prev_ids = self._active_token_ids
            while not self._active_markets_q.empty():
                try:
                    self._active_token_ids = self._active_markets_q.get_nowait()
                except asyncio.QueueEmpty:
                    break
            new_ids = self._active_token_ids - prev_ids
            removed_ids = prev_ids - self._active_token_ids
            self._stale_skip_warned -= removed_ids       # re-warn if market re-enters and is still stale
            self._pre_res_flatten_warned -= removed_ids  # reset so re-entry re-logs the warning
            if removed_ids and self._cancel_q is not None:
                for _tid in removed_ids:
                    await self._cancel_q.put(CancelAll(_tid))
                    log.info(f"DESELECTED [{_tid[:8]}] — cancelling resting quotes")

            while not self._skew_updates_q.empty():
                try:
                    self._skew_updates_q.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # Check force-reprice regardless of path
            now = time.monotonic()
            if (now - self._last_force_reprice) >= self.FORCE_REPRICE_INTERVAL:
                force = True

            # Decide which markets to reprice
            if force or new_ids:
                tokens_to_check = self._active_token_ids
            else:
                # Only reprice the ticked markets that we're actively quoting
                tokens_to_check = triggered_ids & self._active_token_ids

            # CLOB staleness circuit breaker: if the feed has gone silent, pull all
            # quotes and wait. Quoting on stale prices is worse than not quoting.
            clob_age = time.time() - self._app.feeds.last_feed_update.get("clob", 0)
            if clob_age > self.CLOB_STALE_SECONDS:
                if not self._clob_stale:
                    self._clob_stale = True
                    log.warning(
                        f"CLOB feed stale ({clob_age:.0f}s) — pulling all quotes"
                    )
                    if self._cancel_q is not None:
                        await self._cancel_q.put(CancelAll("*"))
                continue  # skip reprice until feed recovers

            if self._clob_stale:
                log.info("CLOB feed recovered — resuming quotes")
                self._clob_stale = False

            if tokens_to_check:
                if self._maker.global_in_cooldown():
                    # During global cooldown, still drain reduce_only positions (over-cap or orphaned).
                    drain = [t for t in tokens_to_check if t in self._maker.reduce_only_markets]
                    if drain:
                        await self._reprice(drain, force=force or bool(new_ids), new_ids=new_ids)
                    else:
                        log.debug("global inventory cooldown active — skipping reprice")
                else:
                    await self._reprice(tokens_to_check, force=force or bool(new_ids), new_ids=new_ids)

            if force:
                self._last_force_reprice = now
