from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class WeatherForecast:
    city_slug: str
    ecmwf_temp: Optional[float]          # today's forecast (backward compat)
    hrrr_temp: Optional[float]           # today's forecast (backward compat)
    metar_temp: Optional[float]          # current METAR observation
    sigma_ecmwf: float
    sigma_hrrr: float
    fetched_at: datetime
    # Multi-day forecasts keyed by local date string "YYYY-MM-DD"
    ecmwf_by_date: dict = None           # date -> °C or °F
    hrrr_by_date:  dict = None           # date -> °C or °F (US only)

    def __post_init__(self):
        if self.ecmwf_by_date is None:
            self.ecmwf_by_date = {}
        if self.hrrr_by_date is None:
            self.hrrr_by_date = {}
