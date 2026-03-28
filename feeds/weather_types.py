from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class WeatherForecast:
    city_slug: str
    ecmwf_temp: Optional[float]
    hrrr_temp: Optional[float]
    metar_temp: Optional[float]
    sigma_ecmwf: float
    sigma_hrrr: float
    fetched_at: datetime
