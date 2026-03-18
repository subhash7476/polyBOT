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
MIN_EV_THRESHOLD = 0.03
POLYMARKET_FEE = 0.02           # Verify current taker fee
ADVERSE_SELECTION_PENALTY = 0.005
MIN_MARKET_LIQUIDITY = 10_000

# Signal agreement
MIN_SIGNALS_REQUIRED = 2

# Exposure
MAX_GROUP_EXPOSURE_PCT = 0.25   # 25% of bankroll per direction-bucket

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
}

# Intervals (seconds)
FEDWATCH_POLL_INTERVAL = 300
ONCHAIN_POLL_INTERVAL = 300

# URLs
DERIBIT_WS_URL = "wss://www.deribit.com/ws/api/v2"
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLYMARKET_CLOB_URL = "https://clob.polymarket.com"

# Credentials
POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
POLY_API_KEY = os.getenv("POLY_API_KEY", "")
GLASSNODE_API_KEY = os.getenv("GLASSNODE_API_KEY", "")
