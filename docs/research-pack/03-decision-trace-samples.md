# Decision Trace Samples

These examples are intended to show how the bot reasons, not to expose implementation details.

## Sample A: Weather Trade That Looked Good But Was Bad

Market:
- "Will the highest temperature in NYC be 47F or below on March 29?"

Trade style:
- BUY_YES

Observed at entry:
- market probability was very low
- model probability was very high
- EV appeared strongly positive

Why the trade was entered:
- forecast-based signal heavily disagreed with market pricing
- the bot interpreted that disagreement as edge

What later appeared to be true:
- the weather model likely confused current/overnight conditions with end-of-day maximum temperature
- trade would have resolved as a loser

Why this sample matters:
- demonstrates that large apparent edge can reflect model misuse, not market mispricing

## Sample B: Crypto Threshold Trade

Market:
- "Will Bitcoin reach $90,000 by December 31, 2026?"

Trade style:
- BUY_NO

Observed at entry:
- model probability for YES materially below market probability
- net EV still positive after friction assumptions

Why the trade was entered:
- threshold pricing looked rich relative to model assumptions

Known caveat:
- this is long-dated, so it does not validate short-horizon edge

## Sample C: Crypto First-To-Hit Trade

Market:
- "Will Bitcoin hit $60k or $80k first?"

Trade style:
- BUY_YES

Observed at entry:
- model probability near certainty
- market price much lower than model estimate

Why this sample matters:
- good example of the bot preferring strongly one-sided directional crypto setups
- also a good place for reviewers to challenge whether the model is overconfident

## Sample D: Expired Crypto-Fast Cleanup

Market:
- "XRP Up or Down - February 24, 2:00AM-2:05AM ET"

Problem:
- stale market entered the ledger because expiry handling was wrong

Fix:
- expiry parsing corrected
- expired positions are now force-closed as housekeeping cleanup

Why this sample matters:
- shows the difference between operational bugs and real trading edge

## What To Share With Reviewers
For each sample, provide:
- question text
- side taken
- model probability
- market probability
- EV
- position size
- major contributing signals
- eventual outcome if known

Avoid sharing:
- private code
- API secrets
- infra details
