"""
WeatherFeed — fetches ECMWF, HRRR (GFS seamless), and METAR forecasts
for all 20 Polymarket weather cities every WEATHER_POLL_INTERVAL seconds.
All HTTP calls are synchronous (requests) wrapped in run_in_executor.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

from config import WEATHER_POLL_INTERVAL, LOCATIONS
from feeds.base import BaseFeed
from feeds.weather_types import WeatherForecast
from calibration.weather_calibration import WeatherCalibration

log = logging.getLogger(__name__)

# Timezone map for Open-Meteo requests
TIMEZONES = {
    "nyc":          "America/New_York",
    "chicago":      "America/Chicago",
    "miami":        "America/New_York",
    "dallas":       "America/Chicago",
    "seattle":      "America/Los_Angeles",
    "atlanta":      "America/New_York",
    "london":       "Europe/London",
    "paris":        "Europe/Paris",
    "munich":       "Europe/Berlin",
    "ankara":       "Europe/Istanbul",
    "seoul":        "Asia/Seoul",
    "tokyo":        "Asia/Tokyo",
    "shanghai":     "Asia/Shanghai",
    "singapore":    "Asia/Singapore",
    "lucknow":      "Asia/Kolkata",
    "tel-aviv":     "Asia/Jerusalem",
    "toronto":      "America/Toronto",
    "sao-paulo":    "America/Sao_Paulo",
    "buenos-aires": "America/Argentina/Buenos_Aires",
    "wellington":   "Pacific/Auckland",
}


def _fetch_ecmwf(city_slug: str, loc: dict) -> tuple[Optional[float], dict]:
    """
    ECMWF IFS 0.25° with bias correction via Open-Meteo.
    Returns (today_temp, {date: temp}) using the city's local timezone.
    """
    unit = loc.get("unit", "F")
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    tz_name = TIMEZONES.get(city_slug, "UTC")
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit={temp_unit}"
        f"&forecast_days=3&timezone={tz_name}"
        f"&models=ecmwf_ifs025&bias_correction=true"
    )
    for attempt in range(3):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if "error" not in data and "daily" in data:
                by_date: dict = {}
                for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                    if temp is not None:
                        v = round(float(temp), 1) if unit == "C" else round(float(temp))
                        by_date[date] = v
                # "Today" for this city = first date in the local-timezone response
                today_temp = next(iter(by_date.values())) if by_date else None
                return today_temp, by_date
            break
        except Exception as e:
            if attempt < 2:
                import time; time.sleep(2)
            else:
                log.debug(f"[WeatherFeed] ECMWF {city_slug}: {e}")
    return None, {}


def _fetch_hrrr(city_slug: str, loc: dict) -> tuple[Optional[float], dict]:
    """GFS seamless (HRRR+GFS) via Open-Meteo. US cities only, returns (today, {date: temp})."""
    if loc.get("region") != "us":
        return None, {}
    tz_name = TIMEZONES.get(city_slug, "UTC")
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={loc['lat']}&longitude={loc['lon']}"
        f"&daily=temperature_2m_max&temperature_unit=fahrenheit"
        f"&forecast_days=3&timezone={tz_name}"
        f"&models=gfs_seamless"
    )
    for attempt in range(3):
        try:
            data = requests.get(url, timeout=(5, 10)).json()
            if "error" not in data and "daily" in data:
                by_date: dict = {}
                for date, temp in zip(data["daily"]["time"], data["daily"]["temperature_2m_max"]):
                    if temp is not None:
                        by_date[date] = round(float(temp))
                today_temp = next(iter(by_date.values())) if by_date else None
                return today_temp, by_date
            break
        except Exception as e:
            if attempt < 2:
                import time; time.sleep(2)
            else:
                log.debug(f"[WeatherFeed] HRRR {city_slug}: {e}")
    return None, {}


def _fetch_metar(station: str, unit: str) -> Optional[float]:
    """Current observed temperature from airport METAR station."""
    url = f"https://aviationweather.gov/api/data/metar?ids={station}&format=json"
    try:
        data = requests.get(url, timeout=(5, 8)).json()
        if data and isinstance(data, list):
            temp_c = data[0].get("temp")
            if temp_c is not None:
                if unit == "F":
                    return round(float(temp_c) * 9 / 5 + 32)
                return round(float(temp_c), 1)
    except Exception as e:
        log.debug(f"[WeatherFeed] METAR {station}: {e}")
    return None


class WeatherFeed(BaseFeed):
    def __init__(self, state):
        super().__init__("weather")
        self._state = state

    async def _run(self):
        while True:
            try:
                await self._refresh_all()
            except Exception as e:
                log.warning(f"[WeatherFeed] refresh error: {e}")
            await asyncio.sleep(WEATHER_POLL_INTERVAL)

    async def _refresh_all(self):
        loop = asyncio.get_event_loop()
        cal = await loop.run_in_executor(None, WeatherCalibration.load)

        async def _refresh_city(city_slug: str, loc: dict):
            ecmwf_today, ecmwf_by_date = await loop.run_in_executor(None, _fetch_ecmwf, city_slug, loc)
            hrrr_today,  hrrr_by_date  = await loop.run_in_executor(None, _fetch_hrrr,  city_slug, loc)
            metar = await loop.run_in_executor(None, _fetch_metar, loc.get("station", ""), loc.get("unit", "F"))
            return city_slug, WeatherForecast(
                city_slug=city_slug,
                ecmwf_temp=ecmwf_today,
                hrrr_temp=hrrr_today,
                metar_temp=metar,
                sigma_ecmwf=cal.get_sigma(city_slug, "ecmwf"),
                sigma_hrrr=cal.get_sigma(city_slug, "hrrr"),
                fetched_at=datetime.now(timezone.utc),
                ecmwf_by_date=ecmwf_by_date,
                hrrr_by_date=hrrr_by_date,
            )

        tasks = [_refresh_city(slug, loc) for slug, loc in LOCATIONS.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        count = 0
        for result in results:
            if isinstance(result, Exception):
                log.warning(f"[WeatherFeed] city fetch error: {result}")
                continue
            city_slug, wf = result
            async with self._state._lock:
                self._state.feeds.weather_forecasts[city_slug] = wf
            count += 1

        self._state.stamp_feed("weather")
        log.info(f"[WeatherFeed] refreshed {count}/{len(LOCATIONS)} cities")
