# Weather Markets Integration Plan

## Context

The existing bot trades crypto, rates, and macro Polymarket contracts. Polymarket also has a large and growing category of weather temperature markets ("Will the highest temperature in NYC on April 5 be between 50-54°F?"). These are purely forecast-driven and structurally different from crypto/macro contracts, but can flow through the same EV → Kelly → Risk → Executor pipeline. The weather bot article (AlterEgo_eth) shows the forecast methodology: ECMWF + HRRR + METAR ensemble, probabilistic bucket matching via normal distribution, and self-calibrating sigma via MAE tracking. This plan integrates that approach as a first-class market category.

---

## Files to Create (4 new files)

### 1. `feeds/weather_types.py`
Defines `WeatherForecast` dataclass — isolated to avoid circular imports between `state.py` and `feeds/weather.py`.
```python
@dataclass
class WeatherForecast:
    city_slug: str
    ecmwf_temp: float | None
    hrrr_temp: float | None
    metar_temp: float | None
    sigma_ecmwf: float        # calibrated MAE for this city/source
    sigma_hrrr: float
    fetched_at: datetime
```

### 2. `feeds/weather.py` — `WeatherFeed(BaseFeed)`
Polls ECMWF, HRRR, METAR every `WEATHER_POLL_INTERVAL` seconds (default 3600). Wraps synchronous `requests` calls via `asyncio.run_in_executor`.

**LOCATIONS dict** — all 20 cities with airport ICAO coords (defined in this file):
```
nyc/KLGA chicago/KORD miami/KMIA dallas/KDAL seattle/KSEA atlanta/KATL
london/EGLC paris/LFPG munich/EDDM ankara/LTAC
seoul/RKSI tokyo/RJTT shanghai/ZSPD singapore/WSSS lucknow/VILK tel-aviv/LLBG
toronto/CYYZ sao-paulo/SBGR buenos-aires/SAEZ wellington/NZWN
```

**Internal fetch functions (synchronous, run via executor):**
- `_fetch_ecmwf(city_slug, loc)` → GET `api.open-meteo.com/v1/forecast?models=ecmwf_ifs025&bias_correction=true&daily=temperature_2m_max&forecast_days=7`
- `_fetch_hrrr(city_slug, loc)` → same endpoint with `models=gfs_seamless&forecast_days=3` (US only)
- `_fetch_metar(station)` → GET `aviationweather.gov/api/data/metar?ids={station}&format=json`

**`_run()` loop:** for each city: fetch all 3 sources, load calibrated sigma from `WeatherCalibration`, build `WeatherForecast`, write to `state.feeds.weather_forecasts[city_slug]` under lock. Call `state.stamp_feed("weather")`.

### 3. `engine/weather_probability.py`

**`parse_temp_range(question) → (t_low, t_high)`**
Extracts bucket bounds from question text. Handles:
- `"32-40°F"` → `(32, 40)`
- `"below 32°F"` → `(-999, 32)`
- `"above 90°F"` → `(90, 999)`
- `"exactly 72°F"` → `(72, 72)`

**`bucket_prob(forecast_temp, t_low, t_high, sigma) → float`**
P(actual ∈ [t_low, t_high]) using N(forecast_temp, sigma²):
- Edge bucket `t_low == -999`: `Φ((t_high - forecast) / sigma)`
- Edge bucket `t_high == 999`: `1 - Φ((t_low - forecast) / sigma)`
- Regular bucket: `Φ((t_high - forecast) / sigma) - Φ((t_low - forecast) / sigma)`

**`blend_forecasts(ecmwf, hrrr, metar, target_date) → (temp, sigma, confidence)`**
Source selection rules (same as reference bot):
- D+0: METAR 0.5 + HRRR 0.3 + ECMWF 0.2 (if available)
- D+1–2: HRRR 0.6 + ECMWF 0.4
- D+3+: ECMWF only
Returns `confidence=1.0` if 2+ sources, `0.6` if 1 source.

**`build_weather_probability(contract, feeds, weights) → (model_prob, signal_count, engine)`**
1. `city_slug = contract.asset`
2. `t_low, t_high = parse_temp_range(contract.question)`
3. `wf = feeds.weather_forecasts.get(city_slug)` — return `(0.5, 0, engine)` if stale/missing
4. `temp, sigma, conf = blend_forecasts(wf.ecmwf_temp, wf.hrrr_temp, wf.metar_temp, date)`
5. `prior = bucket_prob(temp, t_low, t_high, sigma)`
6. `engine = BayesianEngine(prior=prior)`
7. Add signal `forecast_confidence`: strength = `(conf - 0.5) * 2`, weight = `SIGNAL_WEIGHTS["weather_forecast_confidence"]`
8. If both ECMWF and HRRR available and within 4°F: add signal `forecast_agreement`: strength = `tanh((2.0 - abs(ecmwf - hrrr)) / 2.0)`, weight = `SIGNAL_WEIGHTS["weather_forecast_agreement"]`
9. Return `(engine.probability, engine.signal_count, engine)`

### 4. `calibration/weather_calibration.py` — `WeatherCalibration`

Persists per-city/source sigma in `calibration/weather_sigma.json`.

```python
DEFAULT_SIGMA_F = 4.5   # conservative prior before data accumulates
DEFAULT_SIGMA_C = 2.5

class WeatherCalibration:
    sigmas: dict    # city_slug → source → float
    errors: dict    # city_slug → source → list[float] (rolling 30 samples)

    @classmethod
    def load(cls) → "WeatherCalibration"
    def save(self)
    def get_sigma(self, city_slug, source) → float
    def record_outcome(self, city_slug, source, forecast_temp, actual_temp)
        # appends |forecast - actual|, recalculates sigma = MAE of last 30 samples
```

Also provides `fetch_actual_temp(city_slug, date_str) → float | None` using Visual Crossing API (`VC_KEY`).

---

## Files to Modify (5 existing files)

### `market/state.py`
Add one field to `FeedState`:
```python
from feeds.weather_types import WeatherForecast
weather_forecasts: dict = field(default_factory=dict)  # city_slug → WeatherForecast
```

### `engine/contract_parser.py`
Add at module level:
```python
_WEATHER_RE = re.compile(
    r'\bhighest\s+temperature\b|\blowest\s+temperature\b|\bhigh\s+temp\b'
    r'|\bdegrees?\s*(?:fahrenheit|celsius|[°]?[fFcC])\b'
    r'|\btemperature.*(?:above|below|between|exceed|reach)\b',
    re.IGNORECASE,
)
_WEATHER_CITY_MAP = {"new york": "nyc", "nyc": "nyc", "chicago": "chicago", ...}  # all 20 cities
```

Insert as step 0 in `parse_contract()` (before rates check to avoid misclassification):
```python
if _WEATHER_RE.search(q):
    city_slug = _detect_weather_city(q)
    if city_slug:
        contract.category = "weather"
        contract.asset = city_slug
        contract.direction = "bucket"
        contract.parseable = True
        return contract
```

### `trading/risk.py`
In `_contract_group_key()`, add:
```python
if parsed.category == "weather":
    return f"weather_{parsed.asset}"   # e.g. "weather_nyc"
```
This groups all buckets from the same city/day event under one exposure bucket, preventing multiple correlated positions (only one bucket wins per event).

### `config.py`
```python
# New signal weights
SIGNAL_WEIGHTS["weather_forecast_confidence"] = 0.35
SIGNAL_WEIGHTS["weather_forecast_agreement"]  = 0.25

# New config vars
WEATHER_POLL_INTERVAL = int(os.getenv("WEATHER_POLL_INTERVAL", "3600"))
VC_KEY = os.getenv("VC_KEY", "")
```

### `main.py`
```python
# 1. Imports
from feeds.weather import WeatherFeed
from engine.weather_probability import build_weather_probability

# 2. In trading_loop category dispatch
elif parsed.category == "weather":
    model_prob, signal_count, engine = build_weather_probability(
        parsed, feeds, SIGNAL_WEIGHTS
    )

# 3. In asyncio.gather()
WeatherFeed(state).start(),
```

---

## Data Flow

```
WeatherFeed (hourly)
  ├─ _fetch_ecmwf() via run_in_executor
  ├─ _fetch_hrrr()  via run_in_executor
  └─ _fetch_metar() via run_in_executor
        ↓
  state.feeds.weather_forecasts["nyc"] = WeatherForecast(...)

trading_loop (every 5s)
  └─ parse_contract() → category="weather", asset="nyc"
        └─ build_weather_probability()
              ├─ parse_temp_range(question) → (50, 54)
              ├─ blend_forecasts() → (52°F, sigma=3.8, conf=1.0)
              ├─ bucket_prob(52, 50, 54, 3.8) → 0.42
              ├─ BayesianEngine(prior=0.42)
              ├─ + forecast_confidence signal
              └─ + forecast_agreement signal (if models agree)
                    ↓
        passes_signal_filter() → ≥1 strong signal or 2 signals
        calculate_ev()         → unchanged
        fractional_kelly()     → unchanged
        risk.can_trade()       → group "weather_nyc"
        executor.place_order() → unchanged
```

---

## Resolution & Calibration

Run manually (or via cron) after market resolution:
```bash
python scripts/resolve_weather.py --city nyc --date 2026-04-05
```
Script:
1. Calls `fetch_actual_temp(city_slug, date)` via Visual Crossing API
2. Loads forecast snapshots from fills.jsonl for that city/date
3. Calls `cal.record_outcome(city_slug, source, forecast_temp, actual_temp)` for each source
4. Calls `tracker.record_outcome(token_id, resolved_yes)` for fill outcome tracking
5. Saves updated sigma to `calibration/weather_sigma.json`

Initial sigma = 4.5°F (conservative). Shrinks as data accumulates (rolling 30-sample MAE).

---

## New Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `VC_KEY` | For calibration | `""` | Visual Crossing API key (free tier) |
| `WEATHER_POLL_INTERVAL` | No | `3600` | Seconds between forecast refreshes |

---

## Signal Filter Behavior

- **2 sources available (typical):** `forecast_confidence` + `forecast_agreement` = 2 signals → passes normal `MIN_SIGNALS_REQUIRED = 2` check without any changes to `signal_filter.py`
- **1 source available:** 1 signal only → passes via the existing "strong prior" exception: `abs(prior - 0.5) × 2 ≥ 0.40` (requires bucket_prob ≥ 70%) — no code change needed

---

## Implementation Sequence

| Phase | Steps | Goal |
|---|---|---|
| 1 | Create `weather_types.py`, add field to `state.py`, create `weather_calibration.py`, create `weather.py`, add to `main.py` gather | Feed populates; verify logs |
| 2 | Modify `contract_parser.py` (regex + dispatch) | Weather markets classified correctly |
| 3 | Create `weather_probability.py`, add dispatch in `trading_loop`, add weights to `config.py` | Model probs appear in logs |
| 4 | Add group key to `risk.py` | Risk bucketing for weather markets |
| 5 | Create `scripts/resolve_weather.py` | Sigma calibration after first resolutions |

---

## Verification

```bash
# After Phase 1: check weather feed
python -c "
import asyncio
from market.state import AppState
from feeds.weather import WeatherFeed
state = AppState()
asyncio.run(WeatherFeed(state)._refresh_all())
print(state.feeds.weather_forecasts)
"

# After Phase 2: check contract parsing
python -c "
from engine.contract_parser import parse_contract
c = parse_contract('abc', 'Will the highest temperature in New York on April 5 be between 50-54 degrees Fahrenheit?')
print(c.category, c.asset, c.direction)  # expected: weather nyc bucket
"

# After Phase 3: check probability
python -c "
from engine.weather_probability import build_weather_probability, parse_temp_range, bucket_prob
print(parse_temp_range('be between 50-54 degrees Fahrenheit'))  # (50, 54)
print(bucket_prob(52, 50, 54, 4.5))  # ~0.34
"

# After Phase 5: run bot and check logs for weather markets flowing through pipeline
python main.py 2>&1 | grep -i weather

# Check fills.jsonl for weather market entries
python -c "
from calibration.metrics import print_calibration_report
print_calibration_report('fills.jsonl')
"
```
