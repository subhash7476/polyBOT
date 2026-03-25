import os
from dotenv import load_dotenv

load_dotenv()

# Risk
BANKROLL_USDC = float(os.getenv("BANKROLL_USDC", "500"))
MAX_DAILY_LOSS_PCT = 0.05
MAX_POSITION_PCT = 0.10
MAX_OPEN_POSITIONS = 5
KELLY_FRACTION = 0.05           # 5% default — conservative until calibrated

# EV
MIN_EV_THRESHOLD = float(os.getenv("MIN_EV_THRESHOLD", "0.02"))
POLYMARKET_FEE = float(os.getenv("POLYMARKET_FEE", "0.02"))   # fraction of notional; verify current taker fee
ADVERSE_SELECTION_PENALTY = 0.005
MIN_MARKET_LIQUIDITY = 1_000

# Paper mode position management
PAPER_POSITION_TTL_HOURS = float(os.getenv("PAPER_POSITION_TTL_HOURS", "6"))

# Signal agreement
MIN_SIGNALS_REQUIRED = 2

# Exposure
MAX_GROUP_EXPOSURE_PCT = 0.20   # 20% of bankroll per direction-bucket

# Supported assets
SUPPORTED_CRYPTO_ASSETS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX"]
DERIBIT_DVOL_ASSETS = ["BTC", "ETH", "SOL"]  # others use realized vol estimate

# Paper mode (set PAPER=false in .env to go live)
PAPER = os.getenv("PAPER", "true").lower() != "false"

# Signal weights (calibrate from fills.jsonl after 50+ resolved signals)
SIGNAL_WEIGHTS = {
    "dvol_lognormal":    0.30,
    "vol_skew":          0.15,
    "term_structure":    0.10,
    "iv_rv_spread":      0.10,
    "funding_rate":      0.15,
    "oi_change":         0.10,
    "onchain_netflow":   0.10,
    "macro_dxy":         0.10,
    "fed_cut_prob":      0.10,
    "flatline":          0.20,
    "orderbook_imbalance": 0.10,
    "volume_divergence": 0.10,
}

# Flatline detector
FLATLINE_WINDOW_HOURS = 48.0        # price range computed over this lookback
FLATLINE_EXPIRY_GATE_HOURS = 72.0   # only fires within this many hours of expiry
FLATLINE_THRESHOLD = 0.02           # max range (0.02 = 2 cents on a 0-1 scale)
FLATLINE_MIN_LEADING_PRICE = 0.60   # leading side must be > 60% to fire

# Order book imbalance
OBI_RATIO_HIGH = 2.5    # bid/ask depth ratio > this -> bullish signal
OBI_RATIO_LOW = 0.4     # bid/ask depth ratio < this -> bearish signal
OBI_MIN_READINGS = 3    # must sustain over this many consecutive readings

# Volume-price divergence
VPD_VOLUME_MULTIPLE = 2.0    # current volume must exceed rolling_avg × this
VPD_PRICE_MOVE_MAX = 0.02    # price must NOT have moved more than this (2%)
VPD_LOOKBACK_HOURS = 24      # rolling average window

# Intervals (seconds)
FEDWATCH_POLL_INTERVAL = 300
ONCHAIN_POLL_INTERVAL = 300

# URLs
DERIBIT_WS_URL = "wss://www.deribit.com/ws/api/v2"
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLYMARKET_CLOB_URL = "https://clob.polymarket.com"

# Market discovery / subscription selection
# Empty category filter means "all categories".
MARKET_CATEGORY_FILTER = os.getenv("MARKET_CATEGORY_FILTER", "")
MARKET_SORT_MODE = os.getenv("MARKET_SORT_MODE", "hybrid")
MAX_SUBSCRIBED_MARKETS = int(os.getenv("MAX_SUBSCRIBED_MARKETS", "250"))
PARSEABLE_MARKET_RESERVE = int(os.getenv("PARSEABLE_MARKET_RESERVE", "50"))

# Credentials
POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
POLY_API_KEY = os.getenv("POLY_API_KEY", "")
GLASSNODE_API_KEY = os.getenv("GLASSNODE_API_KEY", "")
SIGNATURE_TYPE      = int(os.getenv("SIGNATURE_TYPE", "0"))   # 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE
FUNDER_ADDRESS      = os.getenv("FUNDER_ADDRESS", "")
RPC_URL             = os.getenv("RPC_URL", "https://polygon-rpc.com")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")
MAX_TRADE_SIZE_USDC = float(os.getenv("MAX_TRADE_SIZE_USDC", "50"))
