"""
Probability model for Polymarket temperature-bucket weather markets.

Flow:
  parse_temp_range(question) → (t_low, t_high)
  blend_forecasts(ecmwf, hrrr, metar, target_date) → (temp, sigma, confidence)
  bucket_prob(temp, t_low, t_high, sigma) → P(actual ∈ bucket)
  build_weather_probability(contract, feeds, weights) → (model_prob, signal_count, engine)
"""
import logging
import math
import re
from datetime import date, datetime, timezone
from typing import Optional

from config import SIGNAL_WEIGHTS
from engine.bayesian import BayesianEngine, Signal

log = logging.getLogger(__name__)

# Fallback sigmas when no calibration data exists
DEFAULT_SIGMA_F = 4.5
DEFAULT_SIGMA_C = 2.5

# Max age of a WeatherForecast before we refuse to trade on it
MAX_FORECAST_AGE_SECONDS = 7200  # 2 hours


# ── Normal CDF (no scipy dependency) ────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Approximation of Φ(x) using math.erfc."""
    return 0.5 * math.erfc(-x / math.sqrt(2))


# ── Bucket parsing ───────────────────────────────────────────────────────────

_BETWEEN_RE = re.compile(r'between\s+(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)', re.IGNORECASE)
_OR_BELOW_RE = re.compile(r'(-?\d+(?:\.\d+)?)\s*(?:°[FC])?\s+or\s+below', re.IGNORECASE)
_OR_HIGHER_RE = re.compile(r'(-?\d+(?:\.\d+)?)\s*(?:°[FC])?\s+or\s+(?:higher|above)', re.IGNORECASE)
_EXACTLY_RE = re.compile(r'exactly\s+(-?\d+(?:\.\d+)?)', re.IGNORECASE)
_RANGE_RE = re.compile(r'(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)\s*°?[FC]', re.IGNORECASE)
# "be 19°C" / "be 19 degrees" — single-value bucket (±0.5° implied by 1° resolution)
_SINGLE_RE = re.compile(r'\bbe\s+(-?\d+(?:\.\d+)?)\s*(?:degrees?(?:\s+(?:celsius|fahrenheit|[CF]))?|°[FC])', re.IGNORECASE)


def parse_temp_range(question: str) -> Optional[tuple]:
    """
    Extract (t_low, t_high) from a temperature bucket question.
    Returns None if unparseable.
    Edge buckets use sentinel values: -999 (no lower bound), 999 (no upper bound).
    """
    m = _OR_BELOW_RE.search(question)
    if m:
        return (-999.0, float(m.group(1)))
    m = _OR_HIGHER_RE.search(question)
    if m:
        return (float(m.group(1)), 999.0)
    m = _EXACTLY_RE.search(question)
    if m:
        v = float(m.group(1))
        return (v, v)
    m = _BETWEEN_RE.search(question)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = _RANGE_RE.search(question)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = _SINGLE_RE.search(question)
    if m:
        # "be 19°C" — Polymarket uses 1° resolution buckets; treat as [N-0.5, N+0.5)
        v = float(m.group(1))
        return (v - 0.5, v + 0.5)
    return None


# ── Probability computation ──────────────────────────────────────────────────

def bucket_prob(forecast_temp: float, t_low: float, t_high: float, sigma: float) -> float:
    """
    P(actual ∈ [t_low, t_high]) assuming actual ~ N(forecast_temp, sigma²).
    """
    if sigma <= 0:
        sigma = DEFAULT_SIGMA_F
    if t_low == -999.0:
        return _norm_cdf((t_high - forecast_temp) / sigma)
    if t_high == 999.0:
        return 1.0 - _norm_cdf((t_low - forecast_temp) / sigma)
    if t_low == t_high:
        # exact match: within ±0.5 of forecast
        return 1.0 if abs(forecast_temp - t_low) <= 0.5 else 0.0
    return _norm_cdf((t_high - forecast_temp) / sigma) - _norm_cdf((t_low - forecast_temp) / sigma)


def blend_forecasts(
    ecmwf: Optional[float],
    hrrr: Optional[float],
    metar: Optional[float],
    sigma_ecmwf: float,
    sigma_hrrr: float,
    region: str = "us",
) -> tuple:
    """
    Returns (blended_temp, effective_sigma, source_confidence).
    Source selection:
      - D+0 US with METAR: METAR 0.5 + HRRR 0.3 + ECMWF 0.2
      - D+0/1 US:           HRRR 0.6 + ECMWF 0.4
      - D+2+ or non-US:     ECMWF only
    confidence = 1.0 if 2+ sources, 0.6 if 1 source, 0.0 if none.
    """
    is_us = region == "us"

    sources = []
    if is_us and metar is not None:
        sources.append(("metar",  metar,  min(sigma_hrrr, sigma_ecmwf) * 0.8, 0.5))
        if hrrr is not None:
            sources.append(("hrrr",   hrrr,   sigma_hrrr,  0.3))
        if ecmwf is not None:
            sources.append(("ecmwf",  ecmwf,  sigma_ecmwf, 0.2))
    elif is_us and hrrr is not None:
        sources.append(("hrrr",  hrrr,  sigma_hrrr,  0.6))
        if ecmwf is not None:
            sources.append(("ecmwf", ecmwf, sigma_ecmwf, 0.4))
    elif ecmwf is not None:
        sources.append(("ecmwf", ecmwf, sigma_ecmwf, 1.0))
    elif hrrr is not None:
        sources.append(("hrrr", hrrr, sigma_hrrr, 1.0))

    if not sources:
        return None, DEFAULT_SIGMA_F, 0.0

    total_w = sum(w for _, _, _, w in sources)
    temp    = sum(t * w for _, t, _, w in sources) / total_w
    sigma   = sum(s * w for _, _, s, w in sources) / total_w
    confidence = 1.0 if len(sources) >= 2 else 0.6

    return round(temp, 1), round(sigma, 2), confidence


# ── Main probability builder ─────────────────────────────────────────────────

def build_weather_probability(contract, feeds, weights: dict):
    """
    Build (model_prob, signal_count, BayesianEngine) for a weather bucket market.
    Returns (0.5, 0, engine) when forecast data is unavailable or stale.
    """
    city_slug = contract.asset
    question  = contract.question

    # Parse bucket range
    rng = parse_temp_range(question)
    if rng is None:
        log.debug(f"[WeatherProb] unparseable bucket: {question[:60]}")
        return 0.5, 0, BayesianEngine(0.5)

    t_low, t_high = rng

    # Fetch weather forecast from FeedState
    wf = feeds.weather_forecasts.get(city_slug)
    if wf is None:
        log.debug(f"[WeatherProb] no forecast for {city_slug}")
        return 0.5, 0, BayesianEngine(0.5)

    # Staleness check
    age = (datetime.now(timezone.utc) - wf.fetched_at).total_seconds()
    if age > MAX_FORECAST_AGE_SECONDS:
        log.debug(f"[WeatherProb] stale forecast for {city_slug} (age={age:.0f}s)")
        return 0.5, 0, BayesianEngine(0.5)

    # Determine region for blend logic
    from config import LOCATIONS
    region = LOCATIONS.get(city_slug, {}).get("region", "other")

    # Use date-specific forecast when available (handles timezone-shifted markets like NZ)
    ecmwf_temp = wf.ecmwf_temp
    hrrr_temp  = wf.hrrr_temp
    if contract.expiry and getattr(wf, "ecmwf_by_date", None):
        target_date = contract.expiry.strftime("%Y-%m-%d")
        if target_date in wf.ecmwf_by_date:
            ecmwf_temp = wf.ecmwf_by_date[target_date]
        if getattr(wf, "hrrr_by_date", None) and target_date in wf.hrrr_by_date:
            hrrr_temp = wf.hrrr_by_date[target_date]

    # Blend forecasts
    temp, sigma, source_confidence = blend_forecasts(
        ecmwf=ecmwf_temp,
        hrrr=hrrr_temp,
        metar=wf.metar_temp,
        sigma_ecmwf=wf.sigma_ecmwf,
        sigma_hrrr=wf.sigma_hrrr,
        region=region,
    )

    if temp is None:
        log.debug(f"[WeatherProb] no usable forecast data for {city_slug}")
        return 0.5, 0, BayesianEngine(0.5)

    # Compute prior probability
    prior = bucket_prob(temp, t_low, t_high, sigma)
    prior = max(0.02, min(0.98, prior))  # clip to avoid log-odds explosion

    engine = BayesianEngine(prior=prior)

    # Signal 1: forecast_confidence — always fires
    conf_strength = (source_confidence - 0.5) * 2.0
    engine.add_signal(Signal(
        name="weather_forecast_confidence",
        strength=conf_strength,
        weight=weights.get("weather_forecast_confidence", 0.35),
        confidence=source_confidence,
    ))

    # Signal 2: forecast_agreement — fires when ECMWF + HRRR both present and agree
    if wf.ecmwf_temp is not None and wf.hrrr_temp is not None:
        diff = abs(wf.ecmwf_temp - wf.hrrr_temp)
        if diff <= 6.0:  # only add signal when models are in rough agreement
            agreement_strength = math.tanh((3.0 - diff) / 3.0)
            engine.add_signal(Signal(
                name="weather_forecast_agreement",
                strength=agreement_strength,
                weight=weights.get("weather_forecast_agreement", 0.25),
                confidence=1.0,
            ))

    log.debug(
        f"[WeatherProb] {city_slug} bucket=({t_low},{t_high}) "
        f"forecast={temp} sigma={sigma} prior={prior:.3f} "
        f"posterior={engine.probability:.3f} signals={engine.signal_count}"
    )

    return engine.probability, engine.signal_count, engine
