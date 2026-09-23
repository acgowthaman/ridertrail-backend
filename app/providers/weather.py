import hashlib
from datetime import date, datetime

import httpx

from app.providers.base import WeatherProvider, WeatherSnapshot


class OpenWeatherMapProvider(WeatherProvider):
    """Real weather provider backed by OpenWeatherMap.

    NOTE: the free-tier 5 day / 3 hour forecast endpoint only covers ~5 days out.
    For trip days beyond that horizon this will still return the closest entry
    it has -- treat it as a rough seasonal estimate, not a precise forecast, and
    swap in a climate-normals data source for longer-lead-time trips.
    """

    FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def get_forecast(self, lat: float, lon: float, target_date: date) -> WeatherSnapshot:
        params = {"lat": lat, "lon": lon, "appid": self.api_key, "units": "metric"}

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(self.FORECAST_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        target_noon = datetime.combine(target_date, datetime.min.time()).replace(hour=12)
        best, best_delta = None, None
        for entry in data.get("list", []):
            ts = datetime.fromtimestamp(entry["dt"])
            delta = abs((ts - target_noon).total_seconds())
            if best is None or delta < best_delta:
                best, best_delta = entry, delta

        if best is None:
            return WeatherSnapshot(condition="Unknown", temp_c=0.0, wind_kmh=0.0, precipitation_probability=0.0)

        return WeatherSnapshot(
            condition=best["weather"][0]["main"],
            temp_c=best["main"]["temp"],
            wind_kmh=best["wind"]["speed"] * 3.6,
            precipitation_probability=best.get("pop", 0.0),
        )


class MockWeatherProvider(WeatherProvider):
    """Deterministic offline weather provider for local dev / tests / demos.

    Hashes (lat, lon, date) into a stable-but-varied forecast so the same
    request always returns the same "weather" without calling a real API.
    """

    CONDITIONS = ["Clear", "Clouds", "Rain", "Thunderstorm", "Snow"]

    async def get_forecast(self, lat: float, lon: float, target_date: date) -> WeatherSnapshot:
        key = f"{lat:.2f},{lon:.2f},{target_date.isoformat()}"
        h = int(hashlib.sha256(key.encode()).hexdigest(), 16)

        condition = self.CONDITIONS[h % len(self.CONDITIONS)]
        temp_c = float(5 + ((h // len(self.CONDITIONS)) % 30) - 5)
        wind_kmh = float((h // 1000) % 60)
        precip = ((h // 97) % 100) / 100.0

        return WeatherSnapshot(
            condition=condition, temp_c=temp_c, wind_kmh=wind_kmh, precipitation_probability=precip
        )
