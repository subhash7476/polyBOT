# Strategy Overview

## Goal
Trade Polymarket contracts where the bot believes market price materially differs from a model-estimated probability, with emphasis on:
- fast-resolving contracts where possible
- structurally mispriced or weakly priced markets
- controlled paper validation before any real-money deployment

## Current Strategy Lanes

### 1. Directional Rates
What it does:
- trades Fed/rates contracts when the internal rate model differs from market pricing

Typical trade shape:
- buy YES on low-priced rate-hike or rate-cut contracts
- buy NO on overconfident no-change / target-price style contracts

Current status:
- operational
- low resolved sample count
- not yet profitability-validated

### 2. Directional Crypto
What it does:
- trades crypto threshold and first-to-hit contracts using spot, dvol, funding, skew, and some macro/context signals

Typical trade shape:
- buy YES on upside/downside thresholds when model probability exceeds price
- buy NO when the YES side appears overpriced

Current status:
- operational
- mostly long-dated contracts in practice
- not yet validated on resolved outcomes

### 3. Crypto Fast Exit / Short-Dated Crypto
What it does:
- intended to manage very short-dated or short-horizon crypto positions with fast exits
- supports take-profit, stop-loss, time-stop, and expired cleanup

Current status:
- lifecycle logic exists
- true fast-market universe on Polymarket appears thin/intermittent
- not yet established as a reliable edge source

### 4. Weather
What it does:
- trades daily temperature contracts using forecast-derived signals

Current status:
- tested
- produced materially bad outcomes in paper
- currently considered unsafe until the weather model is redesigned

## Trade Decision Flow
1. Discover active Polymarket markets.
2. Parse question into category, asset, threshold/direction, and expiry.
3. Build a model probability using category-specific signals.
4. Apply signal filter and liquidity checks.
5. Compute expected value net of modeled frictions.
6. Apply risk checks.
7. Enter paper trade.
8. Track until exit or resolution.

## What The Bot Is Not
- not a true market-making bot
- not a spread-capture maker bot
- not a guaranteed arbitrage system
- not yet a proven profitable trading system

## Current Judgment
The bot has working mechanics but no demonstrated repeatable edge yet. Weather is a known weak lane. Crypto and rates remain hypotheses, not validated profit centers.
