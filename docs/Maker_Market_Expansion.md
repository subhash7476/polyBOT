# Polymarket Category Expansion: Research Report for Your Maker Bot
*Generated: 2026-04-29 | Sources: 14 | Confidence: High*

---

## Executive Summary

Polymarket has exploded to $12B/month in volume (March 2026 record). Your bot currently runs on weather — a data-rich but low-volume niche (~$30-50K/day total across all weather markets). Three categories stand out as high-priority additions: **Economics/Macro** (calendar-driven, high volume per market, similar to weather in structure), **Sports game markets** (highest absolute volume on platform at 39%), and **Crypto price brackets** (5,400+ markets, constant activity). Each has a distinct risk/reward profile for a market-making bot.

---

## 1. Platform Volume Reality (April 2026)

| Category | Share of Volume | 24h Volume | Markets |
|----------|----------------|------------|---------|
| **Sports** | ~39% | ~$97M | 4,000+ |
| **Politics** | ~34% | ~$6.8M | 1,600+ |
| **Crypto** | ~18% | ~$18.6M | 5,400+ |
| **Economics/Macro** | ~3-5% | $2-5M | 157 |
| **Weather** | <1% | ~$30-50K | ~500/week |

Your bot is running on the smallest slice of the platform. Weather is great for strategy validation (data-anchored, clear resolution, low competition) but capped on upside.

---

## 2. Maker Fee Structure (Critical for Bot Economics)

Key facts that directly affect your bot:

- **Makers pay 0% fees** — every limit order is free
- **25% rebate on taker fees** for: sports, politics, finance, tech, weather, economics, culture, geopolitics
- **20% rebate** for crypto (slightly worse)
- **Geopolitics: 0% fees but also 0% rebates** — neutral
- **Quadratic reward scoring**: tighter quotes near mid earn exponentially more rewards; two-sided quoting gets a **3× multiplier**

Your bot is already optimized for the 25% rebate structure. Adding categories at the same rebate rate (economics, sports) is a direct revenue multiplier with no structural changes needed.

---

## 3. Category-by-Category Assessment for Your Bot

### A. Economics / Fed Rates / Macro — **Best First Addition**

**Why it's perfect for your bot:**
- Same binary structure as weather (will X happen by date Y)
- **Calendar-driven resolution** — FOMC dates, CPI release dates, NFP Fridays are all known weeks in advance. You can pre-plan when to widen spreads (data release day) vs. quote normally (in between)
- **Very high volume per market**: Fed April decision = $103M total, $55M in last 30 days. Compare to weather at $20-50K per market
- Long duration (weeks to months) = low adverse selection compared to sports/crypto
- 25% rebate rate (same as weather)
- **Your bot already has macro models built in** (MacroFeed, FedFeed, FRED integration) — this is the most natural extension

**Risk**: Near FOMC/CPI release (±2h), spreads should be widened significantly. News can gap a market 20-40 points in seconds. Outside that window: very low risk.

**Markets to target**: Fed decisions (each FOMC meeting = $20-55M/market), CPI print markets, unemployment print markets, GDP markets. Typically 30-90 day horizons.

---

### B. Sports Game Markets (NBA, Soccer, Tennis) — **High Volume, Manageable Risk**

**Volume is the highest on the entire platform:**
- NBA single game moneyline: $500K–$1M per game
- Soccer (Champions League, EPL): $40K–$100K per game
- Tennis (ATP Masters): $50K–$100K per match
- Total sports daily: ~$97M

**Why it works for a maker bot:**
- Constant supply of new markets (NBA plays 3-5 games/night, soccer leagues are daily)
- Markets open days/weeks before the game — long duration early window is low adverse-selection
- Spreads are wider than equivalent sportsbook markets (retail, not sharp flow)
- 25% rebate rate

**The key risk — adverse selection near game time:**
- In last 30-60 minutes before tip-off: injury news, lineup changes, sharp sportsbook lines all hit. This is when you *must* pull quotes
- In-game live markets: avoid entirely — resolves in real-time with impossible adverse selection
- **Mitigation**: add a `time_to_start` guard — stop quoting any sport market within 60 minutes of scheduled start

**Best starting league for your bot**: NBA moneylines. ~5-8 games/day, each $500K-$1M, clear resolution (official box score), and the season is ongoing through June (NBA Finals).

---

### C. Crypto Price Bracket Markets — **High Count, Lower Rebate**

**5,400+ markets, constant activity, but different risk profile:**

Types available:
- "What price will Bitcoin hit this week?" (weekly, ~$5-25M each)
- "BTC above $X on [date]?" (daily, ~$1-5M each)
- "BTC up or down 5 minutes?" — **avoid**, this is pure HFT adverse selection territory

**Why it can work for your bot:**
- Weekly and monthly price bracket markets resolve on known schedule
- Spreads are reasonable at bracket boundaries (e.g., is BTC in the $90K-$95K bracket = moderate uncertainty)
- Very high market count means even small exposure per market = meaningful total

**Key risks:**
- 20% rebate (not 25%) — slightly worse economics
- BTC/ETH prices move 24/7 — your bot must reprice quotes whenever underlying moves >1%
- Informed traders (crypto-native) are more active here than in weather/macro

**Best approach**: Focus on monthly bracket markets (BTC/ETH price range for the month) rather than daily. More duration = more spread income, less whipsawing.

---

### D. Politics — **Skip for Now**

Politics is 34% of Polymarket volume but has specific problems for automated makers:
- **Whale concentration**: 63% of volume is from top 0.23% of wallets, who actively exploit stale quotes
- **Binary gap risk**: A candidate dropping out or major news can move markets from 50¢ to 2¢ overnight
- Long duration = large inventory accumulation before resolution
- Academic studies show ~25% of sports/politics volume may be wash trading (suspicious activity)

Not recommended for the maker bot at this stage. The adverse selection tail risk is too severe.

---

## 4. Practical Configuration Recommendations

### Priority 1: Add Economics/Macro
```
# In .env — remove 'economics' from MAKER_EXCLUDED_CATEGORIES
MAKER_EXCLUDED_CATEGORIES=politics,geopolitics,crypto
MAKER_MAX_DAYS_TO_RESOLVE=45  # Fed meetings can be 30-45 days out
```

The bot needs one guard: detect if it's within ±2h of a scheduled data release (FOMC, CPI, NFP) and widen spreads 2-3× during that window. The `MacroFeed` already pulls FRED data and economic calendar info.

### Priority 2: Add Sports (NBA first)
```
MAKER_EXCLUDED_CATEGORIES=politics,geopolitics,crypto
```
Add a `hours_to_start` check in the market selector — any market with a game scheduled within 60 minutes should have quotes suspended. The Gamma API includes `end_date` which approximates game start time.

### Priority 3: Crypto weekly brackets
Only after sports is running cleanly. Set tighter inventory caps (crypto moves fast) and monitor markout carefully at T+30s before scaling up.

---

## 5. Expected Impact

| Metric | Current (weather only) | Post-expansion (eco + sports) |
|--------|------------------------|-------------------------------|
| Daily addressable volume | ~$30-50K | ~$100M+ |
| Est. fills/day | 20-30 | 200-500+ |
| Rebate streams | 1 category | 3 categories |
| Adverse selection risk | Low | Manageable with time guards |

---

## Key Takeaways

1. **Economics/Macro is the safest, highest-value first addition** — same binary structure as weather, calendar-driven, 10x higher volume per market, 25% rebate, and your bot already has FRED/macro feeds wired in.

2. **Sports (NBA) is the highest volume opportunity** — but requires a mandatory "pull quotes 60min before game" guard to avoid adverse selection on lineup/injury news.

3. **Crypto weekly bracket markets** are viable but lower rebate (20%) and require a live price feed to keep quotes current — add last.

4. **Stay out of politics and geopolitics** — whale concentration and binary gap risk make these hostile to automated market makers.

5. **Your regime score system is more valuable in volatile categories** (sports, crypto) than in weather — the VPIN signal you built is designed precisely to detect informed flow that would adversely select your quotes.

---

## Sources

1. [PredScope Live Stats](https://predscope.com/stats) — Live Polymarket volume by category, top 10 markets
2. [DeFiRate Polymarket Volume](https://defirate.com/prediction-markets/volume/polymarket/) — Monthly volume breakdown 2025-2026
3. [Types of Markets on Polymarket](https://polymarkets.co.il/en/guide/market-types/) — Category share breakdown, fee structure table
4. [Polymarket 2026 Outlook](https://www.polymarketblog.com/article/polymarket-2026-predictions-outlook) — Growth trends and major market categories
5. [Polymarket Stats 2026](https://www.polymarket101.com/en/docs/overview/polymarket-stats-volume/) — Volume records, category 24h breakdowns
6. [Top 150 Markets by Volume](https://data.tablepage.ai/d/polymarket-top-150-prediction-markets-by-trading-volume) — Historical top markets analysis
7. [Market Making on Polymarket](https://startpolymarket.com/strategies/market-making/) — Fee structure, rebate mechanics, adverse selection deep-dive
8. [Best Polymarket Categories 2026](https://www.tradetheoutcome.com/best-polymarket-categories-trade-2026/) — Category scorecard, accuracy by volume tier, whale concentration data
9. [Polymarket Market Making Guide](https://www.polytrackhq.app/blog/polymarket-market-making) — Spread sizing by market type, reward farming mechanics
10. [Polymarket MM Guide 2025](https://www.polytrackhq.app/blog/polymarket-market-making-guide) — Automated bot setup, bands approach, reward scoring
11. [Polymarket Docs — Market Makers](https://docs.polymarket.com/market-makers/overview) — Official maker overview
12. [Polysized MM Service](https://polysized.com/blog/polymarket-market-making) — Quadratic scoring details, two-sided 3× multiplier
13. [Polymarket Fed Rates Page](https://polymarket.com/economy/fed-rates) — Live Fed rate market volumes ($55M April decision)
14. [Polymarket Fed Rate Cuts 2026](https://polyscope.pro/polymarket-fed-rate-cuts-2026/) — FOMC market analysis, $115M volume breakdown
