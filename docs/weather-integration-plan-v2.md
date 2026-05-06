# Weather Markets Integration Plan (v2 — Improved)

## Context

Polymarket has **233+ active daily temperature markets** with $4M+ trading volume. Format is standardized: "Highest temperature in [City] on [Date]?" with multi-outcome 2-degree buckets. These resolve DAILY — exactly what the bot needs for fast outcome data and signal calibration.

Key facts from live Polymarket weather markets:
- US cities use °F, international cities use °C (Seoul=°C, Hong Kong=°C, NYC=°F)
- Buckets are 2-degree wide: "62-63°F", "28-29°C", with edge buckets like "below 50°F" and "above 72°F"
- Resolution uses specific airport stations (LaGuardia for NYC, etc.)
- Resolution precision: whole degrees Fahrenheit (US) or Celsius (intl)
- Markets launch ~4-5 days before target date, resolve same day

---

## Files to Create (4 new files)

### 1. `feeds/weather_types.py`

```python
@dataclass
class WeatherForecast:
    city_slug: str
    target_date: date               # ← NEW: the date being forecast
    unit: str                       # "F" or "C" — detected from market question
    ecmwf_temp: float | None        # always stored in market's native unit
    gfs_temp: float | None          # ← RENAMED from hrrr — GFS is global, HRRR is US-only
    hrrr_temp: float | None         # US cities only, None for international
    metar_temp: float | None
    sigma: float                    # calibrated MAE for blended forecast
    fetched_at: datetime
```
### 2. `feeds/weather.py` — `WeatherFeed(BaseFeed)`

Polls forecast APIs every `WEATHER_POLL_INTERVAL` seconds (default 3600).

**LOCATIONS dict** — all 20 cities with ICAO codes AND unit:
```python
LOCATIONS = {
    # US cities — Fahrenheit, have HRRR + GFS + ECMWF
    "nyc":          {"icao": "KLGA", "lat": 40.77, "lon": -73.87, "unit": "F", "region": "us"},
    "chicago":      {"icao": "KORD", "lat": 41.98, "lon": -87.90, "unit": "F", "region": "us"},
    "miami":        {"icao": "KMIA", "lat": 25.79, "lon": -80.29, "unit": "F", "region": "us"},
    "dallas":       {"icao": "KDAL", "lat": 32.85, "lon": -96.85, "unit": "F", "region": "us"},
    "seattle":      {"icao": "KSEA", "lat": 47.45, "lon":-122.31, "unit": "F", "region": "us"},
    "atlanta":      {"icao": "KATL", "lat": 33.64, "lon": -84.43, "unit": "F", "region": "us"},
    # International — Celsius, have GFS + ECMWF only (NO HRRR)
    "london":       {"icao": "EGLL", "lat": 51.47, "lon":  -0.46, "unit": "C", "region": "intl"},
    "paris":        {"icao": "LFPG", "lat": 49.01, "lon":   2.55, "unit": "C", "region": "intl"},
    "munich":       {"icao": "EDDM", "lat": 48.35, "lon":  11.79, "unit": "C", "region": "intl"},
    "ankara":       {"icao": "LTAC", "lat": 40.13, "lon":  32.99, "unit": "C", "region": "intl"},
    "seoul":        {"icao": "RKSI", "lat": 37.46, "lon": 126.44, "unit": "C", "region": "intl"},
    "tokyo":        {"icao": "RJTT", "lat": 35.55, "lon": 139.78, "unit": "C", "region": "intl"},
    "shanghai":     {"icao": "ZSPD", "lat": 31.14, "lon": 121.81, "unit": "C", "region": "intl"},
    "singapore":    {"icao": "WSSS", "lat":  1.35, "lon": 103.99, "unit": "C", "region": "intl"},
    "lucknow":      {"icao": "VILK", "lat": 26.76, "lon":  80.88, "unit": "C", "region": "intl"},
    "tel-aviv":     {"icao": "LLBG", "lat": 32.01, "lon":  34.88, "unit": "C", "region": "intl"},
    "toronto":      {"icao": "CYYZ", "lat": 43.68, "lon": -79.63, "unit": "C", "region": "intl"},
    "sao-paulo":    {"icao": "SBGR", "lat":-23.43, "lon": -46.47, "unit": "C", "region": "intl"},
    "buenos-aires": {"icao": "SAEZ", "lat":-34.82, "lon": -58.54, "unit": "C", "region": "intl"},
    "wellington":   {"icao": "NZWN", "lat":-41.33, "lon": 174.81, "unit": "C", "region": "intl"},
}
```
**Internal fetch functions (synchronous, run via executor):**

- `_fetch_ecmwf(loc, target_date)` → Open-Meteo `models=ecmwf_ifs025&daily=temperature_2m_max`
  - Returns temperature in Celsius. Convert to Fahrenheit if `loc["unit"] == "F"`.
- `_fetch_gfs(loc, target_date)` → Open-Meteo `models=gfs_seamless&daily=temperature_2m_max`
  - Global coverage. Returns Celsius, convert as needed.
- `_fetch_hrrr(loc, target_date)` → Open-Meteo `models=hrrr_conus&daily=temperature_2m_max`
  - **US cities ONLY** (`region == "us"`). Skip for international cities.
  - Returns Celsius, convert as needed.
- `_fetch_metar(station)` → `aviationweather.gov/api/data/metar?ids={station}&format=json`
  - Real-time observation. Only useful for D+0 (today). Returns Celsius, convert.

**Unit conversion helper:**
```python
def _c_to_f(c: float) -> float: return c * 9/5 + 32
def _f_to_c(f: float) -> float: return (f - 32) * 5/9
def _to_market_unit(temp_c: float, unit: str) -> float:
    return _c_to_f(temp_c) if unit == "F" else temp_c
```

All temperatures stored in the market's native unit (°F for US, °C for international) so bucket_prob comparisons are direct — no conversion at trading time.

### 3. `engine/weather_probability.py`

**`parse_target_date(question) → date`** ← NEW (was missing in v1)
Extracts the forecast date from the question title:
```python
# "Highest temperature in NYC on March 27?" → date(2026, 3, 27)
# "Highest temperature in Seoul on April 5?" → date(2026, 4, 5)
_DATE_RE = re.compile(
    r'on\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?'
    r'|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
    r'\s+(\d{1,2})', re.IGNORECASE
)
```
Store as `contract.target_date` (new field on ParsedContract).
**`detect_unit(question) → str`** ← NEW (was missing in v1)
```python
def detect_unit(question: str) -> str:
    q = question.lower()
    if "°f" in q or "fahrenheit" in q:
        return "F"
    if "°c" in q or "celsius" in q:
        return "C"
    # Fallback: check city
    for city_slug, loc in LOCATIONS.items():
        if city_slug in q or _city_name(city_slug) in q:
            return loc["unit"]
    return "F"  # default
```

**`parse_temp_range(question) → (t_low, t_high, unit)`** — IMPROVED
Real Polymarket formats found via search (handles all observed patterns):
```python
# Actual Polymarket bucket formats observed:
# "62-63°F"  "28-29°C"  "66-67°F"  → standard 2-degree bucket
# "below 50°F"  "below 10°C"       → lower edge bucket
# "above 72°F"  "above 30°C"       → upper edge bucket
# "78-79°F"                         → standard bucket

_BUCKET_RE = re.compile(
    r'(?:(\d+)\s*[-–]\s*(\d+))\s*°?\s*([fFcC])',          # "62-63°F"
)
_BELOW_RE = re.compile(r'below\s+(\d+)\s*°?\s*([fFcC])', re.I)  # "below 50°F"
_ABOVE_RE = re.compile(r'above\s+(\d+)\s*°?\s*([fFcC])', re.I)  # "above 72°F"
```
Note: buckets are typically 2°F or 2°C wide. The parser doesn't assume this — it reads the actual range from the question.

**`bucket_prob(forecast_temp, t_low, t_high, sigma) → float`** — unchanged from v1
P(actual ∈ [t_low, t_high]) via N(forecast_temp, sigma²).
Edge buckets use one-sided CDF.
**`blend_forecasts(wf: WeatherForecast, target_date) → (temp, sigma, confidence)`** — IMPROVED

Source selection differs by region:

| Horizon | US cities (have HRRR) | International (no HRRR) |
|---------|----------------------|------------------------|
| D+0 (today) | METAR 0.5 + HRRR 0.3 + ECMWF 0.2 | METAR 0.5 + GFS 0.3 + ECMWF 0.2 |
| D+1-2 | HRRR 0.5 + GFS 0.2 + ECMWF 0.3 | GFS 0.5 + ECMWF 0.5 |
| D+3+ | GFS 0.3 + ECMWF 0.7 | GFS 0.3 + ECMWF 0.7 |

Confidence:
- 3 sources available: `1.0`
- 2 sources: `0.8`
- 1 source: `0.5`
- 0 sources: return `(None, None, 0)` — skip market

Sigma selection: use `WeatherCalibration.get_sigma(city_slug, "blended")`. If fewer sources, inflate sigma by 20% per missing source.

**`build_weather_probability(contract, feeds, weights) → (model_prob, signal_count, engine)`**
1. `city_slug = contract.asset`
2. `target_date = contract.target_date` ← NEW: extracted by parser, not inferred
3. `t_low, t_high, unit = parse_temp_range(contract.question)`
4. `wf = feeds.weather_forecasts.get(city_slug)` — skip if stale (>6h old) or missing
5. `temp, sigma, conf = blend_forecasts(wf, target_date)`
6. `prior = bucket_prob(temp, t_low, t_high, sigma)`
7. `engine = BayesianEngine(prior=prior)`
8. Signal: `forecast_confidence` (weight 0.35)
9. Signal: `forecast_agreement` — if ECMWF and GFS within 4° (weight 0.25)
10. Return `(engine.probability, engine.signal_count, engine)`
### 4. `calibration/weather_calibration.py` — IMPROVED

**Persistence fix:** Store the FULL error buffer, not just sigma. On restart, reload error history and recompute sigma. No data loss on crash.

```python
# calibration/weather_sigma.json stores:
{
  "nyc": {
    "blended": {"sigma": 3.2, "errors": [1.1, 2.3, 0.5, ...]},  # rolling 30
    "ecmwf":   {"sigma": 4.1, "errors": [...]},
    "gfs":     {"sigma": 4.8, "errors": [...]},
    "hrrr":    {"sigma": 3.5, "errors": [...]}
  },
  "seoul": {
    "blended": {"sigma": 1.8, "errors": [...]},  # Celsius — naturally tighter
    "ecmwf":   {"sigma": 2.2, "errors": [...]},
    "gfs":     {"sigma": 2.5, "errors": [...]}
    # No HRRR entry — international city
  }
}
```

Default starting sigmas:
- US cities (°F): `4.5`
- International (°C): `2.5`

These are equivalent (~4.5°F ≈ 2.5°C). The default must match the unit system to avoid the model being wildly miscalibrated on first run.

**Resolution data source (dual fallback):**
1. **Primary:** Visual Crossing API (`VC_KEY`) — reliable, rate-limited
2. **Fallback:** Open-Meteo historical endpoint (`archive-api.open-meteo.com`) — free, unlimited, 5-day delay
   - Use for backfill and when VC rate limit is hit

```python
def fetch_actual_temp(city_slug: str, date_str: str, unit: str) -> float | None:
    # Try Visual Crossing first
    temp = _fetch_vc(city_slug, date_str)
    if temp is None:
        # Fallback to Open-Meteo historical (free, no key, 5-day delay)
        temp = _fetch_open_meteo_historical(city_slug, date_str)
    if temp is not None and unit == "F":
        return _c_to_f(temp)  # Open-Meteo returns Celsius
    return temp
```
---

## Files to Modify (5 existing files)

### `market/state.py`
```python
from feeds.weather_types import WeatherForecast
# Add to FeedState:
weather_forecasts: dict = field(default_factory=dict)  # city_slug → WeatherForecast
```

### `engine/contract_parser.py`

Add `target_date` field to `ParsedContract`:
```python
target_date: Optional[date] = None  # for weather markets: the date being forecast
```

Add at module level:
```python
_WEATHER_RE = re.compile(
    r'\bhighest\s+temp(?:erature)?\b|\blowest\s+temp(?:erature)?\b',
    re.IGNORECASE,
)

# Map question city names → city_slug (must match LOCATIONS keys in weather.py)
_WEATHER_CITY_MAP = {
    "new york": "nyc", "nyc": "nyc", "chicago": "chicago",
    "miami": "miami", "dallas": "dallas", "seattle": "seattle",
    "atlanta": "atlanta", "london": "london", "paris": "paris",
    "munich": "munich", "ankara": "ankara", "seoul": "seoul",
    "tokyo": "tokyo", "shanghai": "shanghai", "singapore": "singapore",
    "lucknow": "lucknow", "tel aviv": "tel-aviv", "tel-aviv": "tel-aviv",
    "toronto": "toronto", "são paulo": "sao-paulo", "sao paulo": "sao-paulo",
    "buenos aires": "buenos-aires", "wellington": "wellington",
    "hong kong": "hong-kong",
}
```

Insert as **step 0** in `parse_contract()` — BEFORE rates check:
```python
if _WEATHER_RE.search(q):
    city_slug = _detect_weather_city(q)  # scans _WEATHER_CITY_MAP
    if city_slug:
        contract.category = "weather"
        contract.asset = city_slug
        contract.direction = "bucket"
        contract.target_date = _parse_weather_date(q)  # ← NEW: extract "March 27" → date
        contract.expiry = datetime.combine(
            contract.target_date, time(23, 59), tzinfo=timezone.utc
        ) if contract.target_date else None
        contract.parseable = (contract.target_date is not None)
        return contract
```

Key improvement over v1: `target_date` is explicitly parsed from the question, not inferred from expiry. This tells `blend_forecasts` which forecast horizon to use (D+0 vs D+3).

### `trading/risk.py`
In `_contract_group_key()`:
```python
if parsed.category == "weather":
    date_str = parsed.target_date.isoformat() if parsed.target_date else "unknown"
    return f"weather_{parsed.asset}_{date_str}"
    # e.g. "weather_nyc_2026-04-05" — groups all NYC buckets for same date
```

This prevents buying multiple buckets for the same city/date (only one bucket wins).

### `config.py`
```python
# Weather signal weights
SIGNAL_WEIGHTS["weather_forecast_confidence"] = 0.35
SIGNAL_WEIGHTS["weather_forecast_agreement"]  = 0.25

# Weather config
WEATHER_POLL_INTERVAL = int(os.getenv("WEATHER_POLL_INTERVAL", "3600"))
WEATHER_DEFAULT_SIGMA_F = 4.5    # Fahrenheit cities
WEATHER_DEFAULT_SIGMA_C = 2.5    # Celsius cities
VC_KEY = os.getenv("VC_KEY", "")
```
### `main.py`
```python
# 1. Imports
from feeds.weather import WeatherFeed
from engine.weather_probability import build_weather_probability

# 2. In trading_loop category dispatch (add BEFORE the else/microstructure fallback):
elif parsed.category == "weather":
    model_prob, signal_count, engine = build_weather_probability(
        parsed, feeds, SIGNAL_WEIGHTS
    )

# 3. In asyncio.gather():
WeatherFeed(state).start(),
```

---

## Data Flow

```
WeatherFeed (hourly poll)
  ├─ _fetch_ecmwf(loc, target_date)   → always (global)
  ├─ _fetch_gfs(loc, target_date)     → always (global)
  ├─ _fetch_hrrr(loc, target_date)    → US cities only
  └─ _fetch_metar(station)            → D+0 only
        ↓  all converted to market's native unit (°F or °C)
  state.feeds.weather_forecasts["nyc"] = WeatherForecast(...)

CLOBMonitor (Gamma API discovery)
  └─ "Highest temperature in NYC on March 27?" → parse_contract()
        → category="weather", asset="nyc", target_date=2026-03-27

trading_loop (every 5s)
  └─ build_weather_probability()
        ├─ parse_temp_range("62-63°F") → (62, 63, "F")
        ├─ blend_forecasts(wf, D+1) → (63.2°F, sigma=3.8, conf=1.0)
        ├─ bucket_prob(63.2, 62, 63, 3.8) → 0.10
        ├─ BayesianEngine(prior=0.10)
        ├─ + forecast_confidence signal (strength=1.0, weight=0.35)
        └─ + forecast_agreement signal (if models within 4°)
              ↓
        passes_signal_filter() → 2 signals = passes
        calculate_ev()         → is 0.10 != market price? Trade if EV > 2%
        fractional_kelly()     → size
        risk.can_trade()       → group "weather_nyc_2026-03-27"
        executor.place_order()
```
---

## Resolution & Sigma Calibration

Run daily (cron or manual) after markets resolve:
```bash
python scripts/resolve_weather.py --city nyc --date 2026-03-27
```

Script:
1. `fetch_actual_temp("nyc", "2026-03-27", "F")` — tries Visual Crossing, falls back to Open-Meteo historical
2. Loads forecast snapshots from fills.jsonl for that city/date
3. `cal.record_outcome("nyc", "ecmwf", forecast_temp, actual_temp)` for each source
4. `cal.record_outcome("nyc", "blended", blended_temp, actual_temp)`
5. `tracker.record_outcome(token_id, resolved_yes)` for P&L tracking
6. Saves updated sigmas + error buffers to `calibration/weather_sigma.json`

Sigma starts at default (4.5°F / 2.5°C) and tightens as errors accumulate (rolling 30-sample MAE).

---

## What Changed from v1

| Issue | v1 (original) | v2 (improved) |
|-------|---------------|---------------|
| HRRR for international cities | Silent fallback to ECMWF-only | Explicit: GFS replaces HRRR for intl, separate blend weights |
| °F/°C handling | Not mentioned | `detect_unit()`, all temps converted to market's native unit |
| Target date extraction | Missing entirely | `parse_target_date()` extracts from question, stored as `contract.target_date` |
| `parse_temp_range` patterns | 4 assumed patterns | Regex built from actual Polymarket formats: "62-63°F", "below 50°F", "above 72°F" |
| Sigma persistence | Error buffer in memory only | Full error buffer + sigma persisted in JSON, survives restarts |
| Resolution data source | Visual Crossing only | VC primary + Open-Meteo historical fallback (free, unlimited) |
| Default sigma by unit | Single 4.5 default | 4.5°F for US, 2.5°C for intl (equivalent values) |
---

## Signal Filter Behavior (no changes needed)

- **2 forecast sources agree:** `forecast_confidence` + `forecast_agreement` = 2 signals → passes `MIN_SIGNALS_REQUIRED = 2`
- **1 source only:** 1 signal → passes via "strong prior" exception IF `abs(prior - 0.5) × 2 ≥ 0.40` (bucket_prob ≥ 70% or ≤ 30%)
- **Flatline/OBI/VPD also fire** on weather markets (they apply to all categories) — can add a 3rd signal near resolution

---

## New Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `VC_KEY` | For calibration | `""` | Visual Crossing API key (free tier: 1000 calls/day) |
| `WEATHER_POLL_INTERVAL` | No | `3600` | Seconds between forecast API refreshes |

---

## Implementation Sequence

| Phase | Steps | Verify |
|---|---|---|
| 1 | Create `weather_types.py`, add field to `state.py` | Import works |
| 2 | Create `weather_calibration.py` with persistence | `load()`/`save()` roundtrip test |
| 3 | Create `weather.py` feed, add to `main.py` gather | `FUNNEL` log shows weather forecasts populating |
| 4 | Modify `contract_parser.py` (regex + city map + date extraction) | `parse_contract("abc", "Highest temperature in NYC on March 27?")` → category=weather |
| 5 | Create `weather_probability.py`, add dispatch in `main.py` | Model probs appear in scan logs |
| 6 | Add risk group key to `risk.py`, weights to `config.py` | Weather markets flow through full pipeline |
| 7 | Create `scripts/resolve_weather.py` | Sigma updates after first resolved market |
---

## Verification

```bash
# Phase 1-2: types and calibration
python -c "
from feeds.weather_types import WeatherForecast
from calibration.weather_calibration import WeatherCalibration
cal = WeatherCalibration.load()
print('NYC sigma:', cal.get_sigma('nyc', 'blended'))  # expect 4.5
cal.record_outcome('nyc', 'ecmwf', 65.0, 63.0)
cal.save()
cal2 = WeatherCalibration.load()
print('Errors persisted:', len(cal2.errors.get('nyc', {}).get('ecmwf', [])))  # expect 1
"

# Phase 3: feed populates
python -c "
import asyncio
from market.state import AppState
from feeds.weather import WeatherFeed
state = AppState()
asyncio.run(WeatherFeed(state)._refresh_all())
for city, wf in state.feeds.weather_forecasts.items():
    print(f'{city}: ecmwf={wf.ecmwf_temp} gfs={wf.gfs_temp} hrrr={wf.hrrr_temp} unit={wf.unit}')
"

# Phase 4: parser
python -c "
from engine.contract_parser import parse_contract
tests = [
    ('abc', 'Highest temperature in NYC on March 27?'),
    ('def', 'Highest temperature in Seoul on March 27?'),
    ('ghi', 'Highest temperature in Chicago on April 5?'),
]
for tid, q in tests:
    c = parse_contract(tid, q)
    print(f'{q[:50]}... → cat={c.category} asset={c.asset} date={c.target_date}')
"

# Phase 5: probability
python -c "
from engine.weather_probability import parse_temp_range, bucket_prob
print(parse_temp_range('be between 62-63°F'))    # (62, 63, 'F')
print(parse_temp_range('below 50°F'))             # (-999, 50, 'F')
print(parse_temp_range('above 30°C'))             # (30, 999, 'C')
print(parse_temp_range('be between 28-29°C'))     # (28, 29, 'C')
print(f'P(63.2 in [62,63], sigma=3.8) = {bucket_prob(63.2, 62, 63, 3.8):.3f}')
"

# Full integration: run bot and check weather markets in pipeline
python main.py 2>&1 | grep -iE "weather|FUNNEL"
```

---

## Why Weather Markets Matter for This Bot

1. **Daily resolution** — outcome data in hours, not months. Calibration feedback loop tightens fast.
2. **233+ active markets** — massive expansion of tradeable universe.
3. **Purely forecast-driven** — no "vibes" or political opinion. Model accuracy is measurable.
4. **Structurally mispriced** — most traders don't run ECMWF+GFS+HRRR ensembles. The bot does.
5. **Low correlation to crypto/macro** — portfolio diversification across categories.
6. **$4M+ volume** — enough liquidity for the bot's position sizes.
