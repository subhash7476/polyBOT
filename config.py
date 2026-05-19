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
# skip tokens priced below 3¢ — market effectively resolved
MIN_ENTRY_PRICE = float(os.getenv("MIN_ENTRY_PRICE", "0.03"))
# symmetric ceiling — skip near-certain tokens on the other end
MAX_ENTRY_PRICE = float(os.getenv("MAX_ENTRY_PRICE", "0.97"))
# fraction of notional; verify current taker fee
POLYMARKET_FEE = float(os.getenv("POLYMARKET_FEE", "0.02"))
ADVERSE_SELECTION_PENALTY = 0.005
MIN_MARKET_LIQUIDITY = 1_000

# Paper mode position management
PAPER_POSITION_TTL_HOURS = float(os.getenv("PAPER_POSITION_TTL_HOURS", "48"))
PAPER_USE_POSITION_TTL = os.getenv(
    "PAPER_USE_POSITION_TTL", "true").lower() == "true"

# Category-level TTL (hours). If a category is not listed, PAPER_POSITION_TTL_HOURS applies.
CATEGORY_TTL_HOURS: dict = {
    "weather":  float(os.getenv("TTL_WEATHER_HOURS",  "2")),
    "crypto":   float(os.getenv("TTL_CRYPTO_HOURS",  "720")),   # 30 days
    "rates":    float(os.getenv("TTL_RATES_HOURS",   "720")),
    "macro":    float(os.getenv("TTL_MACRO_HOURS",   "720")),
    "election": float(os.getenv("TTL_ELECTION_HOURS", "2160")),  # 90 days
    "event":    float(os.getenv("TTL_EVENT_HOURS",   "2160")),
}
TRACKED_POSITIONS_FILE = os.getenv("TRACKED_POSITIONS_FILE", "positions.jsonl")
RESOLUTION_POLL_INTERVAL_SECONDS = int(
    os.getenv("RESOLUTION_POLL_INTERVAL_SECONDS", "120"))

# Signal agreement
MIN_SIGNALS_REQUIRED = 2

# Exposure
MAX_GROUP_EXPOSURE_PCT = 0.20   # 20% of bankroll per direction-bucket

# Supported assets
SUPPORTED_CRYPTO_ASSETS = ["BTC", "ETH", "SOL",
                           "XRP", "BNB", "DOGE", "ADA", "AVAX", "HYPE"]
DERIBIT_DVOL_ASSETS = ["BTC", "ETH", "SOL"]  # others use realized vol estimate

# Paper mode (set PAPER=false in .env to go live)
PAPER = os.getenv("PAPER", "true").lower() != "false"
# Shadow Live Mode: connect to live markets, simulate fills from real trade events.
# Requires PAPER=true (no real orders placed). Default ON in paper mode —
# Poisson paper fills are only useful for plumbing smoke tests.
# Set SHADOW=false to revert to Poisson mode if needed.
SHADOW = os.getenv("SHADOW", "true").lower() == "true"

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
    # Falcon signals — start conservative; recalibrate after 50+ resolved signals
    "falcon_whale_consensus":  0.20,
    "falcon_cross_platform":   0.25,
}

# Falcon agent IDs (each routes to a different dataset on the unified endpoint)
FALCON_AGENT_LEADERBOARD = int(
    os.getenv("FALCON_AGENT_LEADERBOARD",   "584"))  # 15d filtered leaderboard
FALCON_AGENT_TOP_TRADERS = int(
    os.getenv("FALCON_AGENT_TOP_TRADERS",   "579"))  # rolling leaderboard
# deep per-wallet analytics
FALCON_AGENT_WALLET_360 = int(os.getenv("FALCON_AGENT_WALLET_360",    "581"))
FALCON_AGENT_MARKET_INSIGHTS = int(os.getenv(
    "FALCON_AGENT_MARKET_INSIGHTS", "575"))  # market activity + whale flags

# Falcon poll intervals (seconds)
FALCON_LEADERBOARD_POLL_INTERVAL = int(
    os.getenv("FALCON_LEADERBOARD_POLL_INTERVAL",   "900"))   # 15 min
FALCON_WALLET_POLL_INTERVAL = int(
    os.getenv("FALCON_WALLET_POLL_INTERVAL",        "300"))   # 5 min
FALCON_MARKET_INSIGHTS_POLL_INTERVAL = int(
    os.getenv("FALCON_MARKET_INSIGHTS_POLL_INTERVAL", "120"))  # 2 min

# Leaderboard filters: only track wallets meeting these thresholds
FALCON_MIN_WIN_RATE = float(os.getenv("FALCON_MIN_WIN_RATE",   "0.55"))   # 55%
FALCON_MIN_PNL_15D = float(
    os.getenv("FALCON_MIN_PNL_15D",    "5000"))   # $5k in 15d
FALCON_MIN_TRADES_15D = int(
    os.getenv("FALCON_MIN_TRADES_15D",   "20"))     # 20 trades

# Whale market control threshold — above this we widen spreads
FALCON_WHALE_CONTROL_PCT = float(
    os.getenv("FALCON_WHALE_CONTROL_PCT", "60.0"))  # top-1 wallet owns >60%

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
MAX_SUBSCRIBED_MARKETS = int(os.getenv("MAX_SUBSCRIBED_MARKETS", "500"))
PARSEABLE_MARKET_RESERVE = int(os.getenv("PARSEABLE_MARKET_RESERVE", "150"))
# Guaranteed minimum slots for categories with feed-based models (crypto + weather).
# Prevents election/event markets (huge volume) from crowding out tradeable categories.
MODEL_CATEGORY_MIN_SLOTS = int(os.getenv("MODEL_CATEGORY_MIN_SLOTS", "80"))

# Credentials
POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
POLY_API_KEY = os.getenv("POLY_API_KEY", "")
FALCON_API_KEY = os.getenv("FALCON_API_KEY", "")
FALCON_BASE_URL = os.getenv(
    "FALCON_BASE_URL", "https://narrative.agent.heisenberg.so/api/v2/semantic/retrieve/parameterized")
GLASSNODE_API_KEY = os.getenv("GLASSNODE_API_KEY", "")
SPORTRADAR_API_KEY = os.getenv("SPORTRADAR_API_KEY", "")
FEC_API_KEY = os.getenv("FEC_API_KEY", "")
# 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE
SIGNATURE_TYPE = int(os.getenv("SIGNATURE_TYPE", "0"))
FUNDER_ADDRESS = os.getenv("FUNDER_ADDRESS", "")
RPC_URL = os.getenv("RPC_URL", "https://1rpc.io/matic")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
MAX_TRADE_SIZE_USDC = float(os.getenv("MAX_TRADE_SIZE_USDC", "50"))

WEATHER_POLL_INTERVAL = int(os.getenv("WEATHER_POLL_INTERVAL", "3600"))
FAST_RESOLVE_PRIORITY = os.getenv(
    "FAST_RESOLVE_PRIORITY", "true").lower() == "true"
VC_KEY = os.getenv("VC_KEY", "")

# ── Maker bot: Inventory / Risk ────────────────────────────────────────────
MAKER_MAX_INVENTORY_PER_MARKET = float(
    os.getenv("MAKER_MAX_INVENTORY_PER_MARKET",  "20"))
MAKER_MAX_TOTAL_INVENTORY = float(
    os.getenv("MAKER_MAX_TOTAL_INVENTORY",       "700"))
MAKER_MAX_DAILY_LOSS_PCT = float(
    os.getenv("MAKER_MAX_DAILY_LOSS_PCT",        "0.03"))

# Per-category inventory caps (0 = fall back to MAKER_MAX_INVENTORY_PER_MARKET)
MAKER_WEATHER_INV_CAP = float(
    os.getenv("MAKER_WEATHER_INV_CAP",           "5"))
MAKER_SPORTS_INV_CAP = float(os.getenv("MAKER_SPORTS_INV_CAP",            "0"))
MAKER_POLITICS_INV_CAP = float(
    os.getenv("MAKER_POLITICS_INV_CAP",          "0"))
MAKER_CRYPTO_INV_CAP = float(os.getenv("MAKER_CRYPTO_INV_CAP",            "0"))
MAKER_FINANCE_INV_CAP = float(
    os.getenv("MAKER_FINANCE_INV_CAP",           "0"))
MAKER_ENTERTAINMENT_INV_CAP = float(
    os.getenv("MAKER_ENTERTAINMENT_INV_CAP",     "0"))


def _build_category_caps() -> dict[str, float]:
    caps: dict[str, float] = {"weather": MAKER_WEATHER_INV_CAP}
    for cat, val in [
        ("sports",        MAKER_SPORTS_INV_CAP),
        ("politics",      MAKER_POLITICS_INV_CAP),
        ("crypto",        MAKER_CRYPTO_INV_CAP),
        ("finance",       MAKER_FINANCE_INV_CAP),
        ("entertainment", MAKER_ENTERTAINMENT_INV_CAP),
    ]:
        if val > 0:
            caps[cat] = val
    return caps


MAKER_CATEGORY_CAPS: dict[str, float] = _build_category_caps()

# ── Maker bot: Quote sizing ────────────────────────────────────────────────
MAKER_QUOTE_SIZE = float(os.getenv("MAKER_QUOTE_SIZE",    "5"))
MAKER_LADDER_LEVELS = int(os.getenv("MAKER_LADDER_LEVELS",   "3"))
MAKER_LEVEL_STEP = float(os.getenv("MAKER_LEVEL_STEP",    "0.01"))

# ── Maker bot: Spread ──────────────────────────────────────────────────────
MAKER_BASE_SPREAD = float(os.getenv("MAKER_BASE_SPREAD", "0.06"))
MAKER_MIN_SPREAD = float(os.getenv("MAKER_MIN_SPREAD",  "0.01"))
MAKER_MAX_SPREAD = float(os.getenv("MAKER_MAX_SPREAD",  "0.15"))

# ── Maker bot: Market selection ────────────────────────────────────────────
MAKER_MAX_ACTIVE_MARKETS = int(os.getenv("MAKER_MAX_ACTIVE_MARKETS",   "100"))
MAKER_MIN_DAILY_VOLUME = float(os.getenv("MAKER_MIN_DAILY_VOLUME",   "1000"))
MAKER_MIN_BID = float(os.getenv("MAKER_MIN_BID",            "0.10"))
MAKER_MAX_BID = float(os.getenv("MAKER_MAX_BID",            "0.90"))

# ── Maker bot: Circuit breakers ────────────────────────────────────────────
MAKER_COOLDOWN_SECONDS = int(
    os.getenv("MAKER_COOLDOWN_SECONDS",         "300"))
MAKER_ADVERSE_COOLDOWN_SECONDS = int(
    os.getenv("MAKER_ADVERSE_COOLDOWN_SECONDS", "1800"))
MAKER_RAPID_FILL_WINDOW = float(
    os.getenv("MAKER_RAPID_FILL_WINDOW",      "5.0"))
MAKER_ADVERSE_MIN_FILLS = int(os.getenv("MAKER_ADVERSE_MIN_FILLS",        "5"))
MAKER_ADVERSE_DIRECTION_PCT = float(
    os.getenv("MAKER_ADVERSE_DIRECTION_PCT",  "0.80"))
MAKER_PRE_RES_HOURS = float(os.getenv("MAKER_PRE_RES_HOURS",          "2.0"))

# ── Maker bot: Category spread multipliers (regime.py baseline) ───────────
MAKER_SPREAD_MULT_FINANCE = float(
    os.getenv("MAKER_SPREAD_MULT_FINANCE",       "1.0"))
MAKER_SPREAD_MULT_CRYPTO = float(
    os.getenv("MAKER_SPREAD_MULT_CRYPTO",        "1.2"))
MAKER_SPREAD_MULT_POLITICS = float(
    os.getenv("MAKER_SPREAD_MULT_POLITICS",      "1.3"))
MAKER_SPREAD_MULT_SPORTS = float(
    os.getenv("MAKER_SPREAD_MULT_SPORTS",        "1.6"))
MAKER_SPREAD_MULT_WEATHER = float(
    os.getenv("MAKER_SPREAD_MULT_WEATHER",       "2.2"))
MAKER_SPREAD_MULT_ENTERTAINMENT = float(
    os.getenv("MAKER_SPREAD_MULT_ENTERTAINMENT", "2.2"))
MAKER_SPREAD_MULT_DEFAULT = float(
    os.getenv("MAKER_SPREAD_MULT_DEFAULT",       "1.5"))

# Category quota tuning: equal-weight base, then small adjustments from recent
# fill rate, markout, and PnL. Keep these close to 0.33 so no single metric dominates.
CATEGORY_QUOTA_FILL_WEIGHT = float(
    os.getenv("CATEGORY_QUOTA_FILL_WEIGHT",      "0.34"))
CATEGORY_QUOTA_MARKOUT_WEIGHT = float(
    os.getenv("CATEGORY_QUOTA_MARKOUT_WEIGHT",   "0.33"))
CATEGORY_QUOTA_PNL_WEIGHT = float(
    os.getenv("CATEGORY_QUOTA_PNL_WEIGHT",       "0.33"))

# ── Maker bot: VPIN / NegRisk ──────────────────────────────────────────────
MAKER_VPIN_STALE_SECONDS = float(
    os.getenv("MAKER_VPIN_STALE_SECONDS",        "180"))
MAKER_NEGRISK_SIBLING_THRESHOLD = float(
    os.getenv("MAKER_NEGRISK_SIBLING_THRESHOLD", "0.80"))

# Live pilot controls (Step 19)
# Comma-separated list of categories to trade in live mode (empty = all)
LIVE_PILOT_CATEGORIES: list[str] = [c.strip() for c in os.getenv(
    "LIVE_PILOT_CATEGORIES", "").split(",") if c.strip()]
LIVE_PILOT_MAX_TRADE_SIZE_USDC = float(
    os.getenv("LIVE_PILOT_MAX_TRADE_SIZE_USDC", "25"))
LIVE_PILOT_MAX_EXPOSURE_PCT = float(
    os.getenv("LIVE_PILOT_MAX_EXPOSURE_PCT", "0.05"))

LOCATIONS: dict = {
    "nyc":          {"lat": 40.7772,  "lon": -73.8726, "name": "New York City",  "station": "KLGA", "unit": "F", "region": "us"},
    "chicago":      {"lat": 41.9742,  "lon": -87.9073, "name": "Chicago",        "station": "KORD", "unit": "F", "region": "us"},
    "miami":        {"lat": 25.7959,  "lon": -80.2870, "name": "Miami",          "station": "KMIA", "unit": "F", "region": "us"},
    "dallas":       {"lat": 32.8471,  "lon": -96.8518, "name": "Dallas",         "station": "KDAL", "unit": "F", "region": "us"},
    "seattle":      {"lat": 47.4502,  "lon": -122.3088, "name": "Seattle",        "station": "KSEA", "unit": "F", "region": "us"},
    "atlanta":      {"lat": 33.6407,  "lon": -84.4277, "name": "Atlanta",        "station": "KATL", "unit": "F", "region": "us"},
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
    "toronto":      {"lat": 43.6772,  "lon": -79.6306, "name": "Toronto",        "station": "CYYZ", "unit": "C", "region": "ca"},
    "sao-paulo":    {"lat": -23.4356, "lon": -46.4731, "name": "Sao Paulo",      "station": "SBGR", "unit": "C", "region": "sa"},
    "buenos-aires": {"lat": -34.8222, "lon": -58.5358, "name": "Buenos Aires",   "station": "SAEZ", "unit": "C", "region": "sa"},
    "wellington":   {"lat": -41.3272, "lon":  174.8052, "name": "Wellington",     "station": "NZWN", "unit": "C", "region": "oc"},
    # Additional cities active on Polymarket weather markets
    "hong-kong":     {"lat":  22.3080, "lon":  113.9185, "name": "Hong Kong",      "station": "VHHH", "unit": "C", "region": "asia"},
    "beijing":       {"lat":  40.0799, "lon":  116.5838, "name": "Beijing",        "station": "ZBAA", "unit": "C", "region": "asia"},
    "shenzhen":      {"lat":  22.6393, "lon":  113.8107, "name": "Shenzhen",       "station": "ZGSZ", "unit": "C", "region": "asia"},
    "chongqing":     {"lat":  29.7192, "lon":  106.6420, "name": "Chongqing",      "station": "ZUCK", "unit": "C", "region": "asia"},
    "taipei":        {"lat":  25.0777, "lon":  121.2330, "name": "Taipei",         "station": "RCTP", "unit": "C", "region": "asia"},
    "milan":         {"lat":  45.6306, "lon":    8.7281, "name": "Milan",          "station": "LIMC", "unit": "C", "region": "eu"},
    "madrid":        {"lat":  40.4719, "lon": -3.5626, "name": "Madrid",         "station": "LEMD", "unit": "C", "region": "eu"},
    "warsaw":        {"lat":  52.1657, "lon":   20.9671, "name": "Warsaw",         "station": "EPWA", "unit": "C", "region": "eu"},
    "austin":        {"lat":  30.1975, "lon": -97.6664, "name": "Austin",         "station": "KAUS", "unit": "F", "region": "us"},
    "denver":        {"lat":  39.8561, "lon": -104.6737, "name": "Denver",         "station": "KDEN", "unit": "F", "region": "us"},
    "houston":       {"lat":  29.9844, "lon": -95.3414, "name": "Houston",        "station": "KIAH", "unit": "F", "region": "us"},
    "los-angeles":   {"lat":  33.9425, "lon": -118.4081, "name": "Los Angeles",    "station": "KLAX", "unit": "F", "region": "us"},
    "san-francisco": {"lat":  37.6213, "lon": -122.3790, "name": "San Francisco",  "station": "KSFO", "unit": "F", "region": "us"},
}
