# Polymarket Liquidity Incentives: Full Research Report
*Generated: 2026-05-02 | Sources: 15+ | Confidence: High*

## Executive Summary

Polymarket runs **three stacking revenue streams** for market makers on the international CLOB (docs.polymarket.com — the platform this bot runs against):

1. **Liquidity Rewards** — daily USDC paid for resting orders scored by tightness and book depth (no fill required)
2. **Maker Rebates** — 20–25% of taker fees redistributed daily on fills
3. **Spread Capture** — round-trip P&L from bid-ask spread (what the bot already tracks)

The bot is already set up to capture streams 2 and 3. Stream 1 (Liquidity Rewards) requires fetching per-market incentive parameters from the CLOB API and ensuring quotes stay within bounds — **this is the gap to close**.

---

## 1. International Liquidity Rewards (docs.polymarket.com)

### Scoring Formula (7-Equation System)

Every minute (10,080 samples per 7-day epoch), the CLOB samples each maker's resting orders:

**Equation 1 — Single order score:**
```
S(v, s) = ((v - s) / v)² × b
```
- `v` = `max_incentive_spread` (market-specific, in cents from mid)
- `s` = your order's actual distance from midpoint (in cents)
- `b` = in-game multiplier (>1 during live events)
- Score = **0** if `s >= v` (outside spread limit)
- Score = **b** if `s = 0` (at midpoint) — so tighter = quadratically better

**Example:** If `max_incentive_spread = 3¢`:
- Order at mid (0¢ away): score = 1.0
- Order 1¢ away: score = (2/3)² = 0.44
- Order 2¢ away: score = (1/3)² = 0.11
- Order 3¢ away: score = 0

**Equations 2–3 — Two-sided aggregation:**  
Scores YES bids + NO asks together (`Q_one`) and YES asks + NO bids together (`Q_two`).

**Equation 4 — Two-sided gate:**

If midpoint in [0.10, 0.90]:
```
Q_min = max(min(Q_one, Q_two), max(Q_one/3, Q_two/3))
```
Single-sided scoring = at most **1/3** of double-sided.

If midpoint outside [0.10, 0.90] (near-resolved market):
```
Q_min = min(Q_one, Q_two)
```
**Single-sided = zero score** when market price < 10¢ or > 90¢.

**Equations 5–7 — Normalization:**
```
Q_final = (your Q_epoch / sum all Q_epochs) × reward_pool
```
Rewards are proportional share of pool, not fixed rate.

### Per-Market Parameters (fetch via CLOB API)

Two fields on every market object control eligibility:
- `min_incentive_size` — minimum order size in shares (orders below = zero score)
- `max_incentive_spread` — max distance from mid in cents (orders beyond = zero score)

Endpoint: `GET https://clob.polymarket.com/clob-market-info?condition_id=<ID>`

### Minimum Holding Time
Orders must be active on the book for **≥ 3.5 seconds** to be eligible. Anti-flicker rule.

### Payout
- Calculated at **midnight UTC daily**
- Credited as USDC to maker wallet on Polygon
- No claim step, no gas required
- Minimum **$1.00** per payout cycle

### Current Reward Pools (May 2026)
| Market Type | Pool Per Game |
|---|---|
| Champions League | $24,000 |
| NBA Games | $7,700 |
| EPL Games | $10,000 |
| IPL Games | $4,500 |
| UFC Main Card | $4,250 |
| MLB Games | $1,650 |
| ATP Tour | $1,450 |
| CS2 A-Tier | $5,500 |
| League of Legends A-Tier | $5,500 |
| La Liga / Serie A / Bundesliga | $3,000–$3,300 |

Monthly total across all markets: **$5M+ in April 2026**, peaks $8M around major events.

---

## 2. Maker Rebates Program (docs.polymarket.com)

Separate from Liquidity Rewards. Funded by taker fees. Requires fills (not just resting orders).

### Formula
```
fee_equivalent = C × feeRate × p × (1 - p)
rebate = (your_fee_equivalent / total_fee_equivalent) × rebate_pool
```
Where C = shares traded, p = price.

### Rebate Rates by Category
| Category | Taker Fee Rate | Maker Rebate |
|---|---|---|
| Sports | 3.0% | **25%** of taker fees |
| Finance/Politics | 4.0% | **25%** |
| Economics/Culture/Weather | 5.0% | **25%** |
| Crypto | 7.2% | **20%** |
| Geopolitics | 0% | None (fee-free) |

**Example:** 1,000 shares fill at 0.50 in a sports market:
- Taker fee = 1,000 × 0.03 × 0.50 × 0.50 = **$7.50**
- Your rebate share = proportional to your fill volume
- A maker with 30% market share gets **~$1.88** from that trade

Check `feesEnabled: true` on market object to confirm eligibility.

### Payout
- Credited instantly on fill
- Daily USDC to maker wallet
- Minimum $1.00 accrual

---

## 3. What the Bot Currently Has

| Feature | Status | Notes |
|---|---|---|
| `post_only=True` on all GTC orders | ✅ Already done | `maker/order_manager.py:79` — guarantees maker status |
| Two-sided quoting | ✅ Already done | Bot quotes both YES and NO tokens |
| WebSocket price subscriptions | ✅ Already done | `CLOBMonitor` uses WS for repricing |
| `min_incentive_size` fetching | ❌ Missing | Orders below min size = zero score |
| `max_incentive_spread` fetching | ❌ Missing | Orders beyond max spread = zero score |
| `custom_feature_enabled: true` on WS | ❌ Missing | Required for `best_bid_ask`, `market_resolved`, `new_market` events |
| Heartbeat endpoint (`POST /heartbeat`) | ❌ Missing | Without it, orders auto-cancel if process goes quiet |
| GTD order type for auto-expiry | ❌ Missing | Useful for quotes that should die before resolution |
| Rewards earnings monitoring | ❌ Missing | `GET /rewards/earnings` and `GET /rewards/percentages` not used |
| Maker rebate tracking | ❌ Missing | Not queried or displayed in dashboard |

---

## 4. Three Gaps to Close (Priority Order)

### Gap 1 — Fetch Incentive Params Per Market (HIGH PRIORITY)

**Why it matters:** If `min_incentive_size = 50` shares and you're quoting 10, you get **zero** liquidity rewards even though the orders are valid.

**What to do:**
```python
# At market selection time, add:
GET /clob-market-info?condition_id=<condition_id>
# Parse: min_incentive_size, max_incentive_spread
# Store on selected market context
# Pass to QuoteEngine so it:
#   (a) sizes orders >= min_incentive_size
#   (b) places quotes within max_incentive_spread of mid
```

### Gap 2 — Heartbeat Endpoint (MEDIUM PRIORITY)

**Why it matters:** Polymarket auto-cancels all open orders if the CLOB doesn't receive a heartbeat from your session within the timeout window. Without it, a quiet period (rate limit, brief disconnect) wipes your book and loses your Q-score for those minutes.

**What to do:**
```python
# Add to maker runner (every ~30s):
POST https://clob.polymarket.com/heartbeat
# Headers: standard L2 auth
# Body: {"markets": [list of active condition_ids]}
```

### Gap 3 — `custom_feature_enabled` on WebSocket (MEDIUM PRIORITY)

**Why it matters:** Without this flag, you don't receive `best_bid_ask` events (only get `price_change` on full book). Also miss `market_resolved` events — so you can't react instantly when a market settles to cancel residual orders.

**What to do:**
```python
# In CLOBMonitor WebSocket subscription payload, add:
{"assets_ids": [...], "type": "market", "custom_feature_enabled": True}
```

---

## 5. Polymarket US (docs.polymarket.us) — Separate Platform

This is a **regulated US-facing** product (separate CLOB, separate wallet). The bot does not currently run against it. Its incentive formula is different:

```
Score = Discount_Factor ^ (ticks_from_best_price) × Order_Size
```

Live game discount factor = **0.30** (aggressive front-weighting at best price).

**Current pools:**
- NBA Playoffs: **$100,000/game** ($85k live)
- PGA Tour: **$250,000/tournament**
- MLB: $25,000/game
- UFC: $25,000 per main card
- Politics (Midterms): $5,000/day
- Macro (CPI/Fed): $10,000/day

**Operating on this platform requires separate onboarding** (US-regulated product). Not relevant for the current bot's immediate roadmap, but notable given pool sizes.

---

## 6. Institutional Market Maker Program

A separate gated tier with contractual obligations and "strong incentives":
- **Contact:** `institutional@qcex.com`
- **Not open** to self-sign-up
- Worth pursuing once you have 300+ live fills and clean markout data to show

---

## 7. Reward Monitoring API Endpoints

These exist and are unused by the bot today:
```
GET /rewards/config           — Active reward configurations
GET /rewards/earnings         — Your earnings by date (authenticated)
GET /rewards/percentages      — Real-time reward % share (authenticated)
GET /rebates?maker=&date=     — Current rebated fees for your address
```
These should be added to the dashboard so you can see what you're earning from the incentive programs in real time.

---

## Key Takeaways

1. **The bot is earning maker rebates and spread P&L already** (streams 2 and 3) whenever it gets fills in fee-enabled markets.

2. **Liquidity Rewards (stream 1) require two parameters** from the CLOB API (`min_incentive_size` and `max_incentive_spread`) to be fetched at market selection time and respected by the QuoteEngine. Without this, orders may score zero regardless of placement.

3. **Two-sided quoting matters a lot.** The formula gives single-sided orders at most 1/3 the score of two-sided. Near-resolved markets (<10¢ or >90¢) give zero for single-sided — the regime score / VPIN pause feature already protects against this.

4. **The quadratic spread penalty is severe.** Order 2/3 of the way to `max_incentive_spread` only scores 1/9. Quoting at or near mid is disproportionately valuable for rewards.

5. **Heartbeat is a reliability must-have.** Without `POST /heartbeat` the CLOB will auto-cancel your orders during any quiet period, resetting Q-score to zero for those samples.

6. **Monthly rewards are substantial** ($5M+ distributed). A well-tuned maker bot capturing even 0.1% of the pool earns ~$5,000/month in liquidity rewards on top of spread P&L and rebates.

---

## Sources
1. [docs.polymarket.com/developers/market-makers/liquidity-rewards](https://docs.polymarket.com/developers/market-makers/liquidity-rewards) — Full scoring formula
2. [docs.polymarket.com/developers/market-makers/maker-rebates-program](https://docs.polymarket.com/developers/market-makers/maker-rebates-program) — Rebate calculation
3. [docs.polymarket.us/incentives/liquidity](https://docs.polymarket.us/incentives/liquidity) — US platform incentive program
4. [docs.polymarket.com/trading/fees](https://docs.polymarket.com/trading/fees) — Fee structure by category
5. [docs.polymarket.com/trading/orders/overview](https://docs.polymarket.com/trading/orders/overview) — Order types, post-only flag
6. [docs.polymarket.com/api-reference/rate-limits](https://docs.polymarket.com/api-reference/rate-limits) — Full rate limit tables
7. [docs.polymarket.com/market-data/websocket/market-channel](https://docs.polymarket.com/market-data/websocket/market-channel) — WebSocket events, custom_feature_enabled

## Methodology
Searched 12+ queries across exa web search and direct URL fetches. Analyzed 15+ primary source pages from official Polymarket documentation on both platforms. Sub-questions: incentive formula mechanics, taker/maker fee structure, order types and post-only support, WebSocket API capabilities, rate limits and heartbeat, institutional program details.
