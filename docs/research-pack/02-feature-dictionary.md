# Feature Dictionary

This is a non-code description of the main data inputs and derived signals used by the bot.

## Market Data

### Polymarket Order Book
Used for:
- best bid / best ask
- midpoint estimate
- spread checks
- simple order book imbalance

Why it matters:
- provides market-implied probability and microstructure context

Known limitation:
- the bot is mostly a taker, so it pays spread rather than earning it

## Crypto Inputs

### Spot Price
Examples:
- BTC spot
- ETH spot
- SOL spot
- XRP spot

Used for:
- threshold probability estimates
- short-horizon momentum signals

### DVOL / Volatility Proxy
Used for:
- long-horizon threshold probability estimation
- rough distributional assumptions

Known limitation:
- probably more useful for threshold/by-date contracts than minute-scale directionals

### Funding Rate
Used for:
- directional sentiment bias
- mild confirmation or contradiction versus spot trend

Known limitation:
- may be too slow or weak for very short-horizon contracts

### Order Book Imbalance
Used for:
- microstructure confirmation
- small weighting on directional confidence

Known limitation:
- vulnerable to noise in thin books

## Rates Inputs

### Fed Cut Probability / Prior
Used for:
- estimating probability of rate outcomes around meeting/event markets

Known limitation:
- sample size is small because many markets are long-dated

## Weather Inputs

### Forecast Confidence
Used for:
- temperature bucket probability estimation

Known limitation:
- prior paper results suggest the weather lane was badly miscalibrated

### Weather Forecast Agreement
Used for:
- confidence weighting across forecast sources

Known limitation:
- did not prevent obviously bad trades in the tested batch

### METAR / Current Observation
Used for:
- current observed weather condition adjustment

Known limitation:
- likely misused for daily-maximum temperature markets, causing severe model errors

## Risk / Sizing Inputs

### Bankroll
Used for:
- per-trade sizing
- group exposure caps
- max daily loss

### Position Limits
Used for:
- max concurrent positions
- reserved slots for specific strategy lanes

## Output Metrics

### Model Probability
The bot’s estimated probability for YES.

### Market Probability
Usually derived from the tradable market side / midpoint.

### Edge
`model_prob - market_prob`

### EV
Expected value after modeled friction assumptions.

## Reviewer Focus
The most useful critique is not “feature X exists.” It is:
- is this feature actually informative for this market type and horizon?
- is the weight too large?
- is the feature being used at the wrong time horizon?
