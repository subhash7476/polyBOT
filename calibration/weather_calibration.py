"""
Persists per-city, per-source forecast MAE (sigma) values.
Updated after each weather market resolution.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import LOCATIONS

log = logging.getLogger(__name__)

DEFAULT_SIGMA_F = 4.5   # °F conservative prior
DEFAULT_SIGMA_C = 2.5   # °C conservative prior
CALIBRATION_MIN_SAMPLES = 5   # minimum samples before sigma updates from default
ROLLING_WINDOW = 30           # keep last N error samples per city/source

_CAL_FILE = Path("calibration/weather_sigma.json")


class WeatherCalibration:
    def __init__(self, sigmas: dict, errors: dict):
        self.sigmas = sigmas   # city_slug → source → float
        self.errors = errors   # city_slug → source → list[float]

    @classmethod
    def load(cls) -> "WeatherCalibration":
        if _CAL_FILE.exists():
            try:
                data = json.loads(_CAL_FILE.read_text(encoding="utf-8"))
                return cls(
                    sigmas=data.get("sigmas", {}),
                    errors=data.get("errors", {}),
                )
            except Exception as e:
                log.warning(f"[WeatherCal] failed to load {_CAL_FILE}: {e}")
        return cls(sigmas={}, errors={})

    def save(self):
        _CAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CAL_FILE.write_text(
            json.dumps({"sigmas": self.sigmas, "errors": self.errors}, indent=2),
            encoding="utf-8",
        )

    def get_sigma(self, city_slug: str, source: str) -> float:
        unit = LOCATIONS.get(city_slug, {}).get("unit", "F")
        default = DEFAULT_SIGMA_F if unit == "F" else DEFAULT_SIGMA_C
        return self.sigmas.get(city_slug, {}).get(source, default)

    def record_outcome(
        self,
        city_slug: str,
        source: str,
        forecast_temp: float,
        actual_temp: float,
    ):
        error = abs(forecast_temp - actual_temp)
        city_errors = self.errors.setdefault(city_slug, {})
        src_errors = city_errors.setdefault(source, [])
        src_errors.append(round(error, 2))
        # rolling window
        if len(src_errors) > ROLLING_WINDOW:
            city_errors[source] = src_errors[-ROLLING_WINDOW:]
        # update sigma if enough samples
        samples = city_errors[source]
        if len(samples) >= CALIBRATION_MIN_SAMPLES:
            mae = sum(samples) / len(samples)
            self.sigmas.setdefault(city_slug, {})[source] = round(mae, 3)
            log.info(f"[WeatherCal] {city_slug}/{source} sigma updated → {mae:.3f} (n={len(samples)})")
        self.save()


def fetch_actual_temp(city_slug: str, date_str: str) -> Optional[float]:
    """Fetch actual high temperature via Visual Crossing API."""
    import requests
    from config import VC_KEY, LOCATIONS
    if not VC_KEY:
        log.warning("[WeatherCal] VC_KEY not set — cannot fetch actual temp")
        return None
    loc = LOCATIONS.get(city_slug, {})
    station = loc.get("station", city_slug)
    unit = loc.get("unit", "F")
    vc_unit = "us" if unit == "F" else "metric"
    url = (
        f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
        f"/{station}/{date_str}/{date_str}"
        f"?unitGroup={vc_unit}&key={VC_KEY}&include=days&elements=tempmax"
    )
    try:
        r = requests.get(url, timeout=(5, 10))
        days = r.json().get("days", [])
        if days and days[0].get("tempmax") is not None:
            return round(float(days[0]["tempmax"]), 1)
    except Exception as e:
        log.warning(f"[WeatherCal] VC fetch failed for {city_slug} {date_str}: {e}")
    return None
