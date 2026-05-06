# Polymarket Edge Research Report
*Generated: 2026-04-27 | Sources: 60+ | Confidence: High on structural findings, Medium on specific return claims*

---

## Executive Summary

Polymarket is not efficiently priced — but it is *informed-trader* efficient, which is a completely different regime. Only **3% of accounts drive all price discovery** (LBS/Yale, 2026). The remaining 97% lose to that minority and to market makers. Your bot can profit as a maker by extracting the **Optimism Tax** (the structural YES overbetting bias worth 2–7 pp per trade depending on category), while using **better information** than the crowd on resolution timing, oracle feeds, and category-specific pricing models. The edges are real, documented, and largely unexploited by retail. The competitive threat is a small set of algorithmic market makers and 3% "skilled" accounts — not the crowd.

---

## 1. The Competitive Landscape

### Who is actually making money

| Archetype | Edge Source | Documented Scale |
|---|---|---|
| **Algorithmic market makers** | Optimism Tax + spread capture | $20M+ in 2024 (platform-wide) |
| **3% skilled directional traders** | Information edge, domain expertise | 44% skill persistence; avg elite ~$10–20K/day |
| **Latency arbitrageurs** | Chainlink oracle lag, cross-platform gaps | $40M extracted Apr 2024–Apr 2025 |
| **Resolution edge traders** | Reading contract rules better than crowd | Undocumented but structurally large |
| **Tail-end accumulators** | Buy YES at 0.92–0.97 during UMA challenge window | 94.2% win rate, $152K net (one documented case) |

### The meta-finding for your bot

The Gomez-Cram/Yale study analyzed every trade across $13.76B of volume: makers and the 3% skilled traders capture **>30% of all gains** while being <3.5% of accounts. The 67% "unlucky" accounts fund them. Your bot as a maker is structurally positioned against the losing majority — that's the business.

---

## 2. Market Microstructure: What the Literature Proves

### The Optimism Tax (most important structural finding)

Becker (2026) analyzed 72.1M trades on Kalshi (same structure as Polymarket):

- **Takers earn -1.12% per trade; makers earn +1.12%** — not from direction, from structure
- Maker profit is **NOT directional forecasting** (Cohen's d ≈ 0.02 between YES and NO maker returns). Makers profit the same whether they buy YES or NO.
- The mechanism: takers are systematically optimism-biased. They overbuy YES at longshot prices. YES longshots underperform NO longshots by **64 percentage points** at 1-cent prices.
- The profit source is entirely the behavioral surplus from taker optimism, not maker information.

**For your bot**: You are correctly positioned as a maker. The Optimism Tax is your annuity. The question is capturing it without adverse selection destroying it.

### Category-Specific Maker-Taker Gaps (Becker 2026)

| Category | Maker-Taker Gap | Notes |
|---|---|---|
| Finance/Economics | **0.17 pp** | Near-efficient — tight competition |
| Politics | **1.02 pp** | Moderate, worth quoting |
| Sports | **2.23 pp** | Strong structural bias |
| Crypto | **2.69 pp** | YES overbetting from "number go up" |
| Entertainment/Media | **4.79–7.32 pp** | Most inefficient — highest edge |
| Weather | **2.57 pp** | But NegRisk complexity; handle carefully |

### Adverse Selection: How Bad Is It?

The Bartlett (2026) Stanford paper (41.6M trades, SSRN) is the definitive work:

- **Single-name markets** (individual candidates, specific verdicts) show the **highest informed price impact** — but behavioral surplus cross-subsidizes this, so maker returns are still positive
- **VPIN** (order flow imbalance) predicts maker losses in single-name markets but NOT in broad-based markets
- Adverse selection spikes near resolution — the literature is unambiguous. The final hours before resolution are the most toxic regime for market makers
- Your **markout T+30s gate** (`avg_markout_30s >= 0`) is correctly placed — this is where the adverse selection signal stabilizes for binary markets

### Breakeven Spread Formula

From the Avellaneda-Stoikov framework, empirically derived:

```
s* = 2 × α × μ

where:
  α = fraction of order flow that is informed
  μ = long-run price impact of an informed trade
  s/2 = half-spread captured per fill

If s/2 < α×μ, you are losing money in expectation regardless of spread earned.
```

**Your fills_markout.jsonl contains everything needed to fit α×μ empirically.** If avg_markout_30s < 0 on a given category, your spread is below breakeven for that category.

---

## 3. Documented Exploitable Inefficiencies (Ranked by Reliability)

### Edge 1: Favourite-Longshot Bias (INVERTED vs Horse Racing)

**Direction**: In modern prediction markets, **longshots are underpriced, favourites are OVERpriced** — opposite to horse racing.

- Contracts at 90–100c resolve YES only **88–89%** of the time (vs 95% implied)
- Contracts at 0–10c resolve YES **11.46%** of the time (vs 5% implied)
- The bias is strongest in the 85–97% range

**Actionable**: Short YES contracts above 90c (or equivalently, systematically bias ask prices on high-probability markets). A portfolio of NO positions on markets priced 82–95% outperforms with statistical significance (QuantPedia 2026).

### Edge 2: Post-Crash Mean Reversion (8.9M data points, 5,629 events)

After a >20% price drop between consecutive 15-min snapshots:

| Time after crash | Average bounce |
|---|---|
| +15 min | +6.6% |
| +30 min | +8.8% |
| +45 min | +10.3% |
| +1 hour | +11.0% |

**Best categories**: Crypto (78% win rate), Sports (79% win rate). **Worst**: Weather (57%, negative P&L), Economics (69%).

**Optimal hold**: 12 hours. Holding to 48h adds only $21 to total P&L vs. $121 at 12h while locking capital 4x.

### Edge 3: Chainlink Oracle Lag on Crypto Markets

Polymarket's 15-min BTC/ETH/SOL/XRP markets settle via Chainlink oracle with ~55 second update lag vs. Binance WebSocket (milliseconds). A bot reading Binance prices can compute P(Up/Down) and enter before the oracle reprices.

**Documented backtest** (5,017 trades, Feb 2026, 3 weeks):

| Asset | Win Rate | Profit |
|---|---|---|
| BTC | 61.5% | $11,550 |
| ETH | 62.7% | $19,268 |
| XRP | 61.4% | $15,599 |
| SOL | 60.1% | $12,826 |

Edge degrades as competition grows. In 2024, arb windows lasted ~12.3 seconds. By early 2026: ~2.7 seconds.

### Edge 4: New Market Launch Mispricing (First 24–48 Hours)

Markets are least efficient at launch. Initial price is often $0.50 (no information). Informed traders arrive over hours to days. The Polymarket maturation study (arXiv 2603.03136) confirms: Kyle's lambda declines by **more than an order of magnitude** as markets mature. Early-lifecycle = widest mispricings.

**For your bot**: Prioritize new markets where your external signal advantage is highest. The `new_market` WebSocket event fires before most traders discover it via polling.

### Edge 5: Resolution Timing / UMA Oracle Gap

**The workflow**: Outcome known in real world → Proposer submits to UMA oracle + $750 USDC bond → **2-hour challenge window** → Market resolves.

During the 2-hour challenge window, buying YES at 0.95–0.99 yields 1–5% with very low directional risk. A documented bot ran this strategy at 270 trades/day for 6 months: **94.2% win rate, $152K net** on $30–50K working capital recycled at ~1.2% average spread.

### Edge 6: Cross-Platform Kalshi/Polymarket Arb

Same question priced 4–7% apart. Execution window: 15–45 seconds. Gross spread 1–4% on liquid, 5–8% on niche. Post-call resolution timing: Kalshi uses AP call time (0–4h lag); Polymarket uses certified results (24–48h lag). This creates a **predictable 12–24 hour structural divergence** after election calls — fully automatable.

### Edge 7: Political Market Underconfidence

Le (2026) on 292M trades: political market prices are **chronically compressed toward 50%** — calibration slopes 0.93–1.83 (should be 1.0). Favorites are systematically underpriced. A model with a better prior on political outcomes (your existing Poisson/Normal models) should lean toward buying political favorites when market price is below model probability.

### Edge 8: Multi-Outcome Overround (NegRisk Arb)

Saguillo et al. (2025, arXiv): **$40M extracted** in one year from:
- YES + NO < $1.00 → buy both (guaranteed profit at resolution)
- Sum of all outcomes < $1.00 in NegRisk → buy all outcomes

Median profit per dollar ~$0.60. Requires fast execution — opportunities close in seconds in liquid markets, minutes in thin markets.

---

## 4. Technical Infrastructure Requirements

### Minimum Competitive Stack

| Layer | Current State | Recommended Upgrade |
|---|---|---|
| **Polygon RPC** | `polygon-rpc.com` (failing) | Alchemy/Ankr WebSocket (15–45ms better) |
| **VPS** | Local dev machine | Co-located VPS near Polygon validators (Singapore for Binance-linked markets) |
| **CLOB WebSocket** | Market channel subscribed | Add `custom_feature_enabled: true` for `new_market` + `market_resolved` events |
| **Sports data** | None | Polymarket's own `wss://sports-api.polymarket.com/ws` (free, no auth) + ESPN API |
| **Resolution monitoring** | None | Scrape AP/ESPN/BLS/Fed per market's `resolutionSource` |
| **Cross-platform** | None | PredictionHunt API for live Kalshi/Polymarket gap alerts |
| **Oracle monitoring** | None | UMA oracle portal monitoring for 2-hour challenge window entry |

### Critical WebSocket Events You're Not Using

Setting `custom_feature_enabled: true` on the market WebSocket channel unlocks:
- **`new_market`** — fires before polling detects it; includes full metadata
- **`market_resolved`** — fires on resolution, triggering UMA tail-end entry
- **`best_bid_ask`** — fires on best quote change only (lower noise than `book`)

Most bots only subscribe to `book` and `price_change`. These three events are the competitive differentiator.

### Sports WebSocket (Completely Free, No Auth)

```
wss://sports-api.polymarket.com/ws
```
Streams live scores, periods (`1H/2H/FT/HT/OT`), and `finished_timestamp` for all active sports markets. When a match ends here, prices haven't moved yet on the CLOB. This is a systematic lead signal.

### API Rate Limits (Key Numbers)

- `POST /order`: 3,500 req/10s burst — you will not hit this
- `DELETE /order`: 3,000 req/10s burst
- `/books` (batch): 500 req/10s — **always batch, never single-book calls**
- Gamma `/markets`: 300 req/10s with `after_cursor` pagination — `offset` returns 422

### Four WebSocket Channels (Most Bots Only Use Two)

| Channel | Endpoint | Auth | Key Use |
|---|---|---|---|
| Market | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | No | Order book, price feed |
| User | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | Yes (API key) | Your own fills/order status |
| Sports | `wss://sports-api.polymarket.com/ws` | No | Live scores, resolution detection |
| RTDS | `wss://ws-live-data.polymarket.com` | Optional | Broad market data, activity feeds |

---

## 5. LLM Strategy: What Actually Works

| Approach | Result | Notes |
|---|---|---|
| **Multi-LLM ensemble** (3 models, trimmed mean, skip if std dev >10%) | Best for qualitative markets | guberm/polymarket-bot |
| **Structured domain models** (Poisson for rates, lognormal for crypto) | Best for data-defined questions | Your existing models — correct approach |
| **RAG + vector DB market matching** (news → market) | Works for news-driven directional trades | Chroma DB + semantic search |
| **Single-LLM for sports/economic data** | Fails | Use structured APIs instead |
| **Cross-market consistency** (KL/JS divergence) | Finds mispricings local checks miss | PolySwarm paper |

**ilovecircle's $2.2M result**: LLM used only to generate API scaffolding. The probability model is a neural net trained on multi-source data. The LLM is a tool, not the oracle.

---

## 6. What to Build Next: Prioritized Roadmap

### Priority 1 — Immediate (fixes the current P&L bleeder)
Already done in this session: weather bid-range guard (0.10/0.90), NegRisk sibling guard, resolution-territory reduce-only skip, weather min-days (8h). These address the root cause of the HK temperature loss.

### Priority 2 — High Impact, Medium Effort

**A. UMA Resolution Monitor + Tail-End Trader**
When `market_resolved` fires on WebSocket OR you detect resolution source consensus (AP + Fox + NBC all agree), enter YES at 0.92–0.98 before price adjusts. Exit at 0.98+. Expected win rate: 90%+. The existing `redeemall_loop` handles redemption.

**B. Category-Specific Spread Calibration from Markout Data**
Fit the breakeven formula `s* = 2αμ` to your `fills_markout.jsonl` per category. Your current `BASE_SPREAD = 0.06` is flat across all categories. Becker's data suggests Finance markets warrant 0.04, Sports/Crypto 0.08+, Entertainment 0.12+. This alone should shift your realized P&L materially.

**C. VPIN Circuit Breaker**
Compute rolling order flow imbalance (fraction of taker buys that are one-sided) over the last N volume bars. If VPIN > 0.6 for 8+ consecutive bars in a single-name market, halt market making on that token. This is the pre-fill signal that predicts the adverse selection events your current post-fill circuit breakers miss.

### Priority 3 — Medium Impact, Lower Effort

**D. Political Market Bias Correction**
Apply a +3–5% probability nudge toward the favorite in political markets to compensate for the systematic underconfidence compression (Le 2026 finding). Your existing signal filter is already Bayesian — this is a prior adjustment.

**E. New Market Priority**
Subscribe to `new_market` WebSocket event. When a new market fires, immediately run `filter_and_rank` on it. If it passes filters, quote it in the first 48 hours with wider spreads (you have the information advantage vs. the crowd arriving over days).

**F. Favourites Short-Bias (NO-leaning on 90c+ markets)**
For markets where `cs.best_bid > 0.87`, bias quote sizes: offer larger ask-side (sell YES/buy NO) than bid-side. The calibration data shows these systematically overprice the favourite. Your existing quote engine can implement this as a size asymmetry without changing prices.

### Priority 4 — Highest Impact, Highest Effort

**G. Sports WebSocket + ESPN Integration**
Wire `wss://sports-api.polymarket.com/ws`. When `finished_timestamp` fires and `ended = true`, immediately trigger tail-end entry on all open sports markets for that game. ESPN API for live scores provides a lead signal before the Polymarket book reprices.

**H. Resolution Source Scraping**
Parse `resolutionSource` from each active market's Gamma metadata. Build per-category scrapers (AP for elections, ESPN for sports, BLS for economic data). When resolution source confirms outcome, enter before UMA proposal is submitted.

**I. Cross-Platform Kalshi Arb**
Stage $5–10K on Kalshi. Monitor PredictionHunt API for Polymarket/Kalshi divergences > 4%. Execute both legs within the ~15-45 second window. Start small to validate execution timing.

---

## 7. Key Risks to Manage

| Risk | Severity | Mitigation |
|---|---|---|
| **Resolution manipulation** (oracle gaming) | High on thin markets | Avoid markets with "widely reported," "significant," or "discretionary" resolution language |
| **Insider trading as adverse selection** | High | Monitor for new accounts + concentrated bets in niche policy markets — these are 7-12x more toxic per dollar |
| **Edge decay** | Medium | Oracle lag was 12s in 2024, now 2.7s. Log competition metrics weekly |
| **Binary jump risk** | High near resolution | `MAKER_MIN_DAYS_TO_RESOLVE` is correct; stack it with the UMA challenge window approach |
| **Thin market manipulation** | Medium | Rasooly & Rozzi: price effects last 60 days post-manipulation. Distrust thin-market prices without external validation |

---

## 8. Top Trader Profiles (Documented Cases)

### Théo ("French Whale") — ~$85M profit, 2024 US Election
- Commissioned private YouGov "neighbour-effect" polls to bypass shy-voter bias
- Diversified across presidential winner, popular vote, state-level, electoral-vote-threshold markets
- Entered in ~$500 increments overnight to minimise market impact
- Win rate: 67% overall on Polymarket

### RetardBoyBilly — $100K/month EV target on $2M bankroll
- Banned from 4/6 Macau casinos for card-counting; applied advantage-play mindset
- Biggest edge: Fed funds rate decisions (follows Powell/Liesman signals 2–3 weeks ahead)
- Cultural edge: Chinese political protocol knowledge
- Targets 50–100% annual gains; max $100K per market

### 0x8dxd — $313 to $2.38M in four months
- 26,738 trades, 98% win rate during competitive window
- Exploited Binance->Polymarket lag on 15-min BTC/ETH/SOL markets
- Warning: edge compresses as competitors arrive

### 0xafEe — 69.5% true win rate
- Specialty: Google search indexes and popular culture
- Low frequency (0.4 trades/day), pure conviction, no hedging

### simonbanza — $1.9M in two weeks (Dec 2025)
- 59% true win rate, 108 bets
- Swing-trader style: exits early on probability moves, does not hold to resolution
- Profit/loss ratio 2.52 despite modest win rate

---

## Sources (Tier 1 — Primary Research)

1. [Becker — Microstructure of Wealth Transfer in Prediction Markets (2026)](https://www.jbecker.dev/research/prediction-market-microstructure) — 72.1M trades, foundational maker-taker analysis
2. [Bartlett — Adverse Selection in Prediction Markets: Kalshi (2026)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6615739) — VPIN, Kyle's lambda, Glosten-Harris adapted to prediction markets
3. [Gomez-Cram et al. — Who Drives Prediction Market Accuracy? (2026)](https://www.coindesk.com/markets/2026/04/26/only-3-of-traders-drive-prediction-markets-accuracy-not-the-crowd-study-finds) — 1.72M accounts, $13.76B volume, 3% finding
4. [Le — Decomposing Crowd Wisdom (2026)](https://arxiv.org/abs/2602.19520) — 292M trades, domain calibration slopes
5. [Reichenbach & Walther — Accuracy, Skill, and Bias on Polymarket (2025)](https://ssrn.com/abstract=5910522) — 124M trades, lifecycle mispricing windows
6. [Saguillo et al. — Arbitrage in Prediction Markets (2025)](https://arxiv.org/abs/2508.03474) — $40M arb quantified
7. [Meister — Kelly Criterion for Prediction Markets (2024)](https://arxiv.org/pdf/2412.14144) — calibration error is first-order, fraction error is second-order
8. [Rasooly & Rozzi — How Manipulable Are Prediction Markets? (2025)](https://arxiv.org/abs/2503.03312) — manipulation effects persist 60 days
9. [Tsang & Yang — Political Shocks and Price Discovery (2026)](https://arxiv.org/abs/2603.03152) — fundamental vs. interpretive shock classification
10. [Oracle Lag Sniper Backtest (2026)](https://github.com/JonathanPetersonn/oracle-lag-sniper/blob/main/RESEARCH.md) — 61.4% win rate across 5,017 trades
11. [Dalen & Shaw — Institutional Liquidity in Prediction Markets (arXiv:2604.10005, 2026)](https://arxiv.org/abs/2604.10005) — welfare effects of institutional MM entry
12. [Clinton & Huang — Accuracy and Efficiency of $2.4B in 2024 Election (2025)](https://ideas.repec.org/p/osf/socarx/d5yx2_v1.html) — Polymarket only 67% accurate vs. 93% PredictIt
13. [PolySwarm — LLM Ensemble for Prediction Markets (arXiv:2604.03888)](https://arxiv.org/abs/2604.03888v1) — 50-persona ensemble, KL/JS divergence cross-market consistency
14. [SimpleFunctions — Longshot Bias Calibration Data](https://simplefunctions.dev/concepts/longshot-bias) — 0-10c contracts resolve YES 11.46%, 90-100c resolve YES 88-89%
15. [dev.to/manja316 — 8.9M Price Points Analysis](https://dev.to/manja316/i-collected-89-million-polymarket-price-points-heres-what-i-found-about-how-markets-really-move-2dil) — post-crash mean reversion quantified

---

## Bottom Line

Your current maker architecture is structurally sound and is the right strategy — you are positioned against the 97% majority. The five changes with the highest expected P&L impact, in order:

1. **Category-specific spread calibration** — flat 6c spread is too tight for Sports/Crypto (should be 8c+). Use fills_markout.jsonl to fit per-category breakeven spreads.
2. **UMA challenge window entry** — the 2-hour tail-end window has a documented 94% win rate and requires only wiring the `market_resolved` WebSocket event.
3. **VPIN-style pre-fill circuit breaker** — current circuit breakers are post-fill (inventory-based). VPIN is a pre-fill signal that stops adverse selection before it accumulates.
4. **Sports WebSocket** — free, no auth, direct lead signal for resolution timing on all sports markets (`wss://sports-api.polymarket.com/ws`).
5. **Favourites short-bias** — for markets >87c, offer larger ask-side size. Calibration data shows these resolve YES only 88-89% vs. 95% implied.
