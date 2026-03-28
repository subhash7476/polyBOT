import os
from dotenv import load_dotenv

load_dotenv()

# Risk
BANKROLL_USDC = float(os.getenv("BANKROLL_USDC", "500"))
MAX_DAILY_LOSS_PCT = 0.05
MAX_POSITION_PCT = 0.10
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "20"))
KELLY_FRACTION = 0.05           # 5% default — conservative until calibrated

# EV
MIN_EV_THRESHOLD = float(os.getenv("MIN_EV_THRESHOLD", "0.02"))
POLYMARKET_FEE = float(os.getenv("POLYMARKET_FEE", "0.02"))   # fraction of notional; verify current taker fee
ADVERSE_SELECTION_PENALTY = 0.005
MIN_MARKET_LIQUIDITY = 1_000

# Paper mode position management
PAPER_POSITION_TTL_HOURS = float(os.getenv("PAPER_POSITION_TTL_HOURS", "6"))
PAPER_USE_POSITION_TTL = os.getenv("PAPER_USE_POSITION_TTL", "false").lower() == "true"
TRACKED_POSITIONS_FILE = os.getenv("TRACKED_POSITIONS_FILE", "positions.jsonl")
RESOLUTION_POLL_INTERVAL_SECONDS = int(os.getenv("RESOLUTION_POLL_INTERVAL_SECONDS", "120"))

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
    "weather_forecast_confidence": 0.35,
    "weather_forecast_agreement":  0.25,
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
PARSEABLE_MARKET_RESERVE = int(os.getenv("PARSEABLE_MARKET_RESERVE", "150"))
# Guaranteed minimum slots for categories with feed-based models (crypto + weather).
# Prevents election/event markets (huge volume) from crowding out tradeable categories.
MODEL_CATEGORY_MIN_SLOTS = int(os.getenv("MODEL_CATEGORY_MIN_SLOTS", "80"))

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

WEATHER_POLL_INTERVAL = int(os.getenv("WEATHER_POLL_INTERVAL", "3600"))
FAST_RESOLVE_PRIORITY = os.getenv("FAST_RESOLVE_PRIORITY", "true").lower() == "true"
VC_KEY = os.getenv("VC_KEY", "")

# Live pilot controls (Step 19)
# Comma-separated list of categories to trade in live mode (empty = all)
LIVE_PILOT_CATEGORIES: list[str] = [c.strip() for c in os.getenv("LIVE_PILOT_CATEGORIES", "").split(",") if c.strip()]
LIVE_PILOT_MAX_TRADE_SIZE_USDC = float(os.getenv("LIVE_PILOT_MAX_TRADE_SIZE_USDC", "25"))
LIVE_PILOT_MAX_EXPOSURE_PCT = float(os.getenv("LIVE_PILOT_MAX_EXPOSURE_PCT", "0.05"))

LOCATIONS: dict = {
    "nyc":          {"lat": 40.7772,  "lon":  -73.8726, "name": "New York City",  "station": "KLGA", "unit": "F", "region": "us"},
    "chicago":      {"lat": 41.9742,  "lon":  -87.9073, "name": "Chicago",        "station": "KORD", "unit": "F", "region": "us"},
    "miami":        {"lat": 25.7959,  "lon":  -80.2870, "name": "Miami",          "station": "KMIA", "unit": "F", "region": "us"},
    "dallas":       {"lat": 32.8471,  "lon":  -96.8518, "name": "Dallas",         "station": "KDAL", "unit": "F", "region": "us"},
    "seattle":      {"lat": 47.4502,  "lon": -122.3088, "name": "Seattle",        "station": "KSEA", "unit": "F", "region": "us"},
    "atlanta":      {"lat": 33.6407,  "lon":  -84.4277, "name": "Atlanta",        "station": "KATL", "unit": "F", "region": "us"},
    "london":       {"lat": 51.5048,  "lon":    0.0495, "name": "London",         "station": "EGLC", "unit": "C", "region": "eu"},
    "paris":        {"lat": 48.9962,  "lon":    2.5979, "name": "Paris",          "station": "LFPG", "unit": "C", "region": "eu"},
    "munich":       {"lat": 48.3537,  "lon":   11.7750, "name": "Munich",         "station": "EDDM", "unit": "C", "region": "eu"},
    "ankara":       {"lat": 40.1281,  "lon":   32.9951, "name": "Ankara",         "station": "LTAC", "unit": "C", "region": "eu"},
    "seoul":        {"lat": 37.4691,  "lon":  126.4505, "name": "Seoul",          "station": "RKSI", "unit": "C", "region": "asia"},
    "tokyo":        {"lat": 35.7647,  "lon":  140.3864, "name": "Tokyo",          "station": "RJTT", "unit": "C", "region": "asia"},
    "shanghai":     {"lat": 31.1443,  "lon":  121.8083, "name": "Shanghai",       "station": "ZSPD", "unit": "C", "region": "asia"},
    "singapore":    {"lat":  1.3502,  "lon":  103.9940, "name": "Singapore",      "station": "WSSS", "unit": "C", "region": "asia"},
    "lucknow":      {"lat": 26.7606,  "lon":   80.8893, "name": "Lucknow",        "station": "VILK", "unit": "C", "region": "asia"},
    "tel-aviv":     {"lat": 32.0114,  "lon":   34.8867, "name": "Tel Aviv",       "station": "LLBG", "unit": "C", "region": "asia"},
    "toronto":      {"lat": 43.6772,  "lon":  -79.6306, "name": "Toronto",        "station": "CYYZ", "unit": "C", "region": "ca"},
    "sao-paulo":    {"lat": -23.4356, "lon":  -46.4731, "name": "Sao Paulo",      "station": "SBGR", "unit": "C", "region": "sa"},
    "buenos-aires": {"lat": -34.8222, "lon":  -58.5358, "name": "Buenos Aires",   "station": "SAEZ", "unit": "C", "region": "sa"},
    "wellington":   {"lat": -41.3272, "lon":  174.8052, "name": "Wellington",     "station": "NZWN", "unit": "C", "region": "oc"},
    # Additional cities active on Polymarket weather markets
    "hong-kong":     {"lat":  22.3080, "lon":  113.9185, "name": "Hong Kong",      "station": "VHHH", "unit": "C", "region": "asia"},
    "beijing":       {"lat":  40.0799, "lon":  116.5838, "name": "Beijing",        "station": "ZBAA", "unit": "C", "region": "asia"},
    "shenzhen":      {"lat":  22.6393, "lon":  113.8107, "name": "Shenzhen",       "station": "ZGSZ", "unit": "C", "region": "asia"},
    "chongqing":     {"lat":  29.7192, "lon":  106.6420, "name": "Chongqing",      "station": "ZUCK", "unit": "C", "region": "asia"},
    "taipei":        {"lat":  25.0777, "lon":  121.2330, "name": "Taipei",         "station": "RCTP", "unit": "C", "region": "asia"},
    "milan":         {"lat":  45.6306, "lon":    8.7281, "name": "Milan",          "station": "LIMC", "unit": "C", "region": "eu"},
    "madrid":        {"lat":  40.4719, "lon":   -3.5626, "name": "Madrid",         "station": "LEMD", "unit": "C", "region": "eu"},
    "warsaw":        {"lat":  52.1657, "lon":   20.9671, "name": "Warsaw",         "station": "EPWA", "unit": "C", "region": "eu"},
    "austin":        {"lat":  30.1975, "lon":  -97.6664, "name": "Austin",         "station": "KAUS", "unit": "F", "region": "us"},
    "denver":        {"lat":  39.8561, "lon": -104.6737, "name": "Denver",         "station": "KDEN", "unit": "F", "region": "us"},
    "houston":       {"lat":  29.9844, "lon":  -95.3414, "name": "Houston",        "station": "KIAH", "unit": "F", "region": "us"},
    "los-angeles":   {"lat":  33.9425, "lon": -118.4081, "name": "Los Angeles",    "station": "KLAX", "unit": "F", "region": "us"},
    "san-francisco": {"lat":  37.6213, "lon": -122.3790, "name": "San Francisco",  "station": "KSFO", "unit": "F", "region": "us"},
}
