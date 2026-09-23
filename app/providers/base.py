from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import List

from app.schemas import Coordinate


@dataclass
class RoutePoint:
    lat: float
    lon: float
    elevation_m: float
    cum_dist_km: float


@dataclass
class RouteResult:
    points: List[RoutePoint]

    @property
    def total_distance_km(self) -> float:
        return self.points[-1].cum_dist_km if self.points else 0.0


@dataclass
class WeatherSnapshot:
    condition: str  # e.g. "Clear", "Rain", "Snow", "Thunderstorm"
    temp_c: float
    wind_kmh: float
    precipitation_probability: float  # 0-1


class RoutingProvider(ABC):
    @abstractmethod
    async def get_route(self, coordinates: List[Coordinate]) -> RouteResult:
        """Return an ordered polyline (with elevation) connecting the given coordinates, in order."""
        raise NotImplementedError


class WeatherProvider(ABC):
    @abstractmethod
    async def get_forecast(self, lat: float, lon: float, target_date: date) -> WeatherSnapshot:
        raise NotImplementedError
