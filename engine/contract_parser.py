import re
import calendar
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
from utils.logger import get_logger

log = get_logger(__name__)

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
RATE_KEYWORDS = ["fed", "rate", "fomc", "basis points", "bps", "cut rates", "hike"]


@dataclass
class ParsedContract:
    token_id: str
    question: str
    asset: Optional[str] = None         # "BTC" | "ETH" | None
    direction: Optional[str] = None     # "above" | "below" | None
    target_price: Optional[float] = None
    expiry: Optional[datetime] = None
    category: str = "crypto"            # "crypto" | "rates" | "unknown"
    parseable: bool = True


def parse_contract(token_id: str, question: str) -> ParsedContract:
    q = question.lower()
    contract = ParsedContract(token_id=token_id, question=question)

    # 1. Rate contracts — detect early, extract direction/target/expiry
    if any(kw in q for kw in RATE_KEYWORDS):
        contract.category = "rates"
        contract.asset = None

        # Direction: cut/lower = below, hike/raise = above, hold = hold
        if any(w in q for w in ["cut", "lower", "reduce", "ease"]):
            contract.direction = "below"
        elif any(w in q for w in ["hike", "raise", "increase", "tighten"]):
            contract.direction = "above"
        elif any(w in q for w in ["hold", "steady", "unchanged"]):
            contract.direction = "hold"
        # "above X%" also implies direction
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

    # 3. Detect asset — try multi-word aliases first (longer first to avoid partial matches)
    for alias in sorted(ASSET_ALIASES, key=len, reverse=True):
        if alias in q:
            contract.asset = ASSET_ALIASES[alias]
            break

    if not contract.asset:
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
