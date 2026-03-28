import re
import calendar
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
from utils.logger import get_logger

log = get_logger(__name__)

# ── Weather market detection ────────────────────────────────────────────────
_WEATHER_RE = re.compile(
    r'\bhighest\s+temperature\b|\blowest\s+temperature\b|\bhigh\s+temp\b'
    r'|\bdegrees?\s*(?:fahrenheit|celsius|[°]?[fFcC])\b'
    r'|\btemperature\b.*(?:above|below|between|exceed|reach)\b',
    re.IGNORECASE,
)

_WEATHER_CITY_MAP: dict = {
    "new york city": "nyc",
    "new york":      "nyc",
    "nyc":           "nyc",
    "chicago":       "chicago",
    "miami":         "miami",
    "dallas":        "dallas",
    "seattle":       "seattle",
    "atlanta":       "atlanta",
    "london":        "london",
    "paris":         "paris",
    "munich":        "munich",
    "ankara":        "ankara",
    "seoul":         "seoul",
    "tokyo":         "tokyo",
    "shanghai":      "shanghai",
    "singapore":     "singapore",
    "lucknow":       "lucknow",
    "tel aviv":      "tel-aviv",
    "tel-aviv":      "tel-aviv",
    "toronto":       "toronto",
    "sao paulo":     "sao-paulo",
    "são paulo":     "sao-paulo",
    "buenos aires":  "buenos-aires",
    "wellington":    "wellington",
}


def _detect_weather_city(q: str) -> Optional[str]:
    for name, slug in sorted(_WEATHER_CITY_MAP.items(), key=lambda x: -len(x[0])):
        if re.search(r'\b' + re.escape(name) + r'\b', q, re.IGNORECASE):
            return slug
    return None


PRICE_PATTERNS = [
    r"\$([0-9,]+(?:\.[0-9]+)?)[mMkK]?",        # $1m, $85k, $85,000, $3,500.50
    r"([0-9,]+(?:\.[0-9]+)?)[kK]\s*(?:USD|USDT|dollars)?",  # 85k USD
]
EXPIRY_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
MACRO_KEYWORDS: dict = {
    "consumer price": "CPI", "cpi": "CPI", "inflation": "CPI",
    "unemployment": "UNEMPLOYMENT", "jobless": "UNEMPLOYMENT",
    "nonfarm": "NFP", "non-farm": "NFP", "payroll": "NFP",
    "gross domestic": "GDP", "gdp": "GDP",
}

ASSET_ALIASES: dict = {
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ethereum": "ETH",
    "sol": "SOL", "solana": "SOL",
    "xrp": "XRP", "ripple": "XRP",
    "bnb": "BNB", "binance coin": "BNB",
    "doge": "DOGE", "dogecoin": "DOGE",
    "ada": "ADA", "cardano": "ADA",
    "avax": "AVAX", "avalanche": "AVAX",
}
DIRECTION_ABOVE = ["above", "over", "exceed", "higher than", "hit", "reach", ">"]
DIRECTION_BELOW = ["below", "under", "drop", "fall below", "<"]
# Use word-boundary regex to avoid false positives: "rate" in "operate",
# "fed" in "federal"/"fedotov", "hike" in valid non-rate contexts.
_RATE_RE = re.compile(
    r'\bfed\b|\brates?\b|\bfomc\b|\bbps\b|\bbasis\s+points\b|\bcut\s+rates\b|\bhike\b',
    re.IGNORECASE,
)
RATE_KEYWORDS = ["fed", "rate", "fomc", "basis points", "bps", "cut rates", "hike"]  # kept for reference
_ELECTION_RE = re.compile(
    r'\belection\b|\bmidterm\b|\bprimary\b|\bpresidential\b|\bsenate\b|\bhouse\s+race\b|\bballot\b|\bvote\b|\bcandidate\b',
    re.IGNORECASE,
)
_EVENT_RE = re.compile(
    r'\bby\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?'
    r'|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|\d{4}|q[1-4])\b'
    r'|\bby\s+end\s+of\b|\bapproved?\b|\bpassed?\b|\blaunched?\b|\breleased?\b',
    re.IGNORECASE,
)

# Questions that look like crypto but aren't price threshold markets
_SKIP_PATTERNS = ["fdv", "market cap", "megaeth", "fully diluted"]

# Short-dated crypto direction markets: "Will BTC go up in the next 5 minutes?"
_SHORT_DATED_RE = re.compile(
    r'(?:will\s+)?(bitcoin|btc|ethereum|eth|solana|sol|xrp|ripple|bnb|doge|dogecoin|cardano|ada|avalanche|avax)'
    r'\s+(?:price\s+)?(?:go\s+)?(up|down|increase|decrease|rise|fall).*?'
    r'(?:next|in(?:\s+the\s+next)?)\s+(\d+)?\s*(min(?:ute)?s?|hour|hours?)',
    re.IGNORECASE,
)


@dataclass
class ParsedContract:
    token_id: str
    question: str
    asset: Optional[str] = None         # "BTC" | "ETH" | None
    direction: Optional[str] = None     # "above" | "below" | "exactly" | None
    target_price: Optional[float] = None
    expiry: Optional[datetime] = None
    category: str = "crypto"            # "crypto" | "rates" | "macro" | "unknown"
    parseable: bool = True
    cut_count: Optional[int] = None     # for "exactly N cuts" rate markets
    T_days: Optional[float] = None      # for short-dated markets; overrides expiry-derived T


def parse_contract(token_id: str, question: str) -> ParsedContract:
    q = question.lower()
    contract = ParsedContract(token_id=token_id, question=question)

    # 0. Weather markets — detect before crypto/macro to avoid misclassification
    if _WEATHER_RE.search(q):
        city_slug = _detect_weather_city(q)
        if city_slug:
            contract.category = "weather"
            contract.asset = city_slug
            contract.direction = "bucket"
            contract.parseable = True
            contract.expiry = _parse_expiry(question)
            log.debug(f"weather market: {q[:60]}")
            return contract

    # 1. Rate contracts — detect early, extract direction/target/expiry
    if _RATE_RE.search(q):
        contract.category = "rates"
        contract.asset = None

        # Annual cut-count markets: "Will N Fed rate cuts happen in 2026?"
        # "Will no Fed rate cuts happen in 2026?"
        no_cuts = re.search(r"\bno\b.*\bfed\b.*\bcut|no fed rate cuts", q)
        n_cuts = re.search(r"will\s+(\d+)\s+fed\s+rate\s+cut", q)
        if no_cuts:
            contract.direction = "exactly"
            contract.cut_count = 0
            contract.target_price = 0.0
        elif n_cuts:
            contract.direction = "exactly"
            contract.cut_count = int(n_cuts.group(1))
            contract.target_price = float(contract.cut_count)
        elif re.search(r"\d+\s+or\s+more\s+fed\s+rate\s+cut", q):
            m = re.search(r"(\d+)\s+or\s+more", q)
            contract.direction = "above"
            contract.cut_count = int(m.group(1)) if m else None
            contract.target_price = float(contract.cut_count) if contract.cut_count else None
        else:
            # Single-meeting markets: direction based on cut/hike/hold keywords
            if any(w in q for w in ["cut", "lower", "reduce", "ease", "decrease"]):
                contract.direction = "below"
            elif any(w in q for w in ["hike", "raise", "increase", "tighten"]):
                contract.direction = "above"
            elif any(w in q for w in ["hold", "steady", "unchanged", "no change", "no rate change", "pause", "maintain"]):
                contract.direction = "hold"
            elif "above" in q:
                contract.direction = "above"
            elif "below" in q:
                contract.direction = "below"

            # Target: bps first, then percentage
            bps_match = re.search(r"(\d+)\s*(?:basis points|bps|bp)", q)
            if bps_match:
                contract.target_price = float(bps_match.group(1))
            else:
                pct_match = re.search(r"(\d+\.?\d*)\s*%", q)
                if pct_match:
                    contract.target_price = float(pct_match.group(1))

        # Expiry
        contract.expiry = _parse_expiry(question)
        if not contract.expiry:
            now = datetime.now(timezone.utc)
            last_day = calendar.monthrange(now.year, now.month)[1]
            contract.expiry = now.replace(day=last_day, hour=23, minute=59, second=0, microsecond=0)

        contract.parseable = (contract.direction is not None)
        return contract

    # 2. Macro contracts (CPI, GDP, unemployment) — detect before crypto
    for keyword in sorted(MACRO_KEYWORDS, key=len, reverse=True):
        if keyword in q:
            contract.category = "macro"
            contract.asset = MACRO_KEYWORDS[keyword]
            pct_match = re.search(r"(\d+\.?\d*)\s*%", q)
            if pct_match:
                contract.target_price = float(pct_match.group(1))
            else:
                # Try plain number (e.g. "200,000 payrolls")
                num_match = re.search(r"(\d[\d,]*)", q)
                if num_match:
                    contract.target_price = float(num_match.group(1).replace(",", ""))
            for word in DIRECTION_ABOVE:
                if word in q:
                    contract.direction = "above"
                    break
            if not contract.direction:
                for word in DIRECTION_BELOW:
                    if word in q:
                        contract.direction = "below"
                        break
            contract.expiry = _parse_expiry(question)
            if not contract.expiry:
                now = datetime.now(timezone.utc)
                last_day = calendar.monthrange(now.year, now.month)[1]
                contract.expiry = now.replace(day=last_day, hour=23, minute=59, second=0, microsecond=0)
            contract.parseable = (contract.direction is not None and contract.target_price is not None)
            return contract

    # 2b. Election/political markets
    if _ELECTION_RE.search(q) and any(w in q for w in ["win", "lose", "elected", "wins"]):
        contract.category = "election"
        contract.direction = "yes"
        contract.expiry = _parse_expiry(question)
        if not contract.expiry:
            now = datetime.now(timezone.utc)
            contract.expiry = now.replace(month=12, day=31, hour=23, minute=59, second=0, microsecond=0)
        contract.parseable = True
        log.debug(f"election market: {question[:60]}")
        return contract

    # 2c. Deadline/event markets ("Will X happen by DATE?")
    # Approval/launch keywords take priority even if an asset alias is present.
    # Exclude price-threshold questions (above/below/reach/exceed) without approval keywords,
    # so unknown-asset price markets remain unparseable.
    _is_approval_event = re.search(r'\bapproved?\b|\bpassed?\b|\blaunched?\b|\breleased?\b', q, re.IGNORECASE)
    _has_price_direction = any(w in q for w in DIRECTION_ABOVE + DIRECTION_BELOW)
    if _EVENT_RE.search(q) and (_is_approval_event or (not any(alias in q for alias in ASSET_ALIASES) and not _has_price_direction)):
        expiry = _parse_expiry(question)
        if expiry:
            contract.category = "event"
            contract.direction = "yes"
            contract.expiry = expiry
            contract.parseable = True
            log.debug(f"event/deadline market: {question[:60]}")
            return contract

    # 2d. Short-dated crypto direction markets ("Will BTC go up in the next 5 minutes?")
    sd_match = _SHORT_DATED_RE.search(q)
    if sd_match:
        raw_asset, raw_direction, raw_count, raw_unit = sd_match.group(1), sd_match.group(2), sd_match.group(3), sd_match.group(4)
        contract.asset = ASSET_ALIASES.get(raw_asset.lower(), raw_asset.upper())
        contract.direction = "above" if raw_direction.lower() in ("up", "increase", "rise") else "below"
        contract.category = "crypto"
        contract.target_price = None
        count = int(raw_count) if raw_count else 1
        unit = raw_unit.lower()
        if unit.startswith("min"):
            contract.T_days = count / 1440
        else:  # hour(s)
            contract.T_days = count / 24
        contract.parseable = True
        log.debug(f"short-dated crypto direction market: {question[:60]}")
        return contract

    # 3. Skip non-price-threshold crypto questions (FDV, market cap, token launches)
    if any(pat in q for pat in _SKIP_PATTERNS):
        contract.parseable = False
        log.debug(f"skip (non-price market): {question[:60]}")
        return contract

    # 4. Detect asset — try multi-word aliases first (longer first to avoid partial matches)
    # Use word-boundary matching to avoid false positives like "sol" in "resolution"
    for alias in sorted(ASSET_ALIASES, key=len, reverse=True):
        if re.search(r'\b' + re.escape(alias) + r'\b', q, re.IGNORECASE):
            contract.asset = ASSET_ALIASES[alias]
            break

    if not contract.asset:
        # Generic binary catch-all — "Will X happen?" → route through microstructure path
        if re.search(r'\bwill\b.*\?', q, re.IGNORECASE):
            contract.category = "event"
            contract.direction = "yes"
            contract.parseable = True
            log.debug(f"generic binary market: {question[:60]}")
            return contract
        contract.parseable = False
        log.debug(f"skip (no asset): {question[:60]}")
        return contract

    # 3. Detect direction
    for word in DIRECTION_ABOVE:
        if word in q:
            contract.direction = "above"
            break
    if not contract.direction:
        for word in DIRECTION_BELOW:
            if word in q:
                contract.direction = "below"
                break

    if not contract.direction:
        contract.parseable = False
        log.debug(f"skip (no direction): {question[:60]}")
        return contract

    # 4. Extract target price
    for pattern in PRICE_PATTERNS:
        match = re.search(pattern, question, re.IGNORECASE)
        if match:
            raw = match.group(1).replace(",", "")
            value = float(raw)
            suffix = match.group(0).lower()
            if "m" in suffix:
                value *= 1_000_000
            elif "k" in suffix:
                value *= 1_000
            contract.target_price = value
            break

    if not contract.target_price:
        contract.parseable = False
        log.debug(f"skip (no price): {question[:60]}")
        return contract

    # 5. Extract expiry (default to end of current month if not found)
    contract.expiry = _parse_expiry(question)
    if not contract.expiry:
        now = datetime.now(timezone.utc)
        last_day = calendar.monthrange(now.year, now.month)[1]
        contract.expiry = now.replace(day=last_day, hour=23, minute=59, second=0, microsecond=0)
        log.debug(f"expiry not found, defaulting to EOM: {question[:60]}")

    return contract


def _parse_expiry(question: str) -> Optional[datetime]:
    q = question.lower()
    now = datetime.now(timezone.utc)

    # Quarter patterns: "Q1 2026", "Q2 2025", etc.
    quarter_match = re.search(r'\bq([1-4])\s+(\d{4})\b', q, re.IGNORECASE)
    if quarter_match:
        quarter = int(quarter_match.group(1))
        year = int(quarter_match.group(2))
        # Last month of the quarter: Q1→Mar(3), Q2→Jun(6), Q3→Sep(9), Q4→Dec(12)
        month = quarter * 3
        last_day = calendar.monthrange(year, month)[1]
        return datetime(year, month, last_day, 23, 59, tzinfo=timezone.utc)

    for month_str, month_num in EXPIRY_MONTHS.items():
        if month_str in q:
            year = now.year if month_num >= now.month else now.year + 1
            day_match = re.search(rf"{month_str}\w*\s+(\d{{1,2}})", q)
            day = int(day_match.group(1)) if day_match else 28
            try:
                return datetime(year, month_num, day, 23, 59, tzinfo=timezone.utc)
            except ValueError:
                return datetime(year, month_num, 28, 23, 59, tzinfo=timezone.utc)

    if "this week" in q or "end of week" in q:
        days_ahead = 7 - now.weekday()
        return now + timedelta(days=days_ahead)

    return None
