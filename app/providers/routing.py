import math
from typing import List

import httpx

from app.geo_utils import haversine_km
from app.providers.base import RoutePoint, RouteResult, RoutingProvider
from app.schemas import Coordinate


class OpenRouteServiceProvider(RoutingProvider):
    """Real routing provider backed by OpenRouteService.

    NOTE: ORS has no dedicated "motorcycle" profile; 'driving-car' is used as the
    closest approximation for a road-going motorcycle. Swap to a custom profile
    (or a self-hosted OSRM/Valhalla instance) if/when one becomes available.
    """

    BASE_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def get_route(self, coordinates: List[Coordinate]) -> RouteResult:
        body = {
            "coordinates": [[c.lon, c.lat] for c in coordinates],
            "elevation": True,
        }
        headers = {"Authorization": self.api_key, "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(self.BASE_URL, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        coords = data["features"][0]["geometry"]["coordinates"]  # [lon, lat, elevation]
        points: List[RoutePoint] = []
        cum = 0.0
        prev = None
        for lon, lat, elev in coords:
            if prev is not None:
                cum += haversine_km(prev[1], prev[0], lat, lon)
            points.append(RoutePoint(lat=lat, lon=lon, elevation_m=elev, cum_dist_km=cum))
            prev = (lon, lat)
        return RouteResult(points=points)


class MockRoutingProvider(RoutingProvider):
    """Deterministic offline routing provider for local dev, tests, and demos
    without an OpenRouteService API key.

    Produces a densified polyline between the requested coordinates (great-circle
    interpolation, NOT real road geometry) with a synthetic elevation profile so
    the itinerary engine's grade/altitude-based speed logic can still be exercised
    end to end.
    """

    POINTS_PER_KM = 2  # sampling density along each leg

    async def get_route(self, coordinates: List[Coordinate]) -> RouteResult:
        points: List[RoutePoint] = []
        cum = 0.0

        for i in range(len(coordinates) - 1):
            a, b = coordinates[i], coordinates[i + 1]
            leg_km = haversine_km(a.lat, a.lon, b.lat, b.lon)
            n_steps = max(2, int(leg_km * self.POINTS_PER_KM))
            start_step = 1 if i > 0 else 0  # avoid duplicating the shared endpoint between legs

            for step in range(start_step, n_steps + 1):
                frac = step / n_steps
                lat = a.lat + (b.lat - a.lat) * frac
                lon = a.lon + (b.lon - a.lon) * frac
                if points:
                    cum += haversine_km(points[-1].lat, points[-1].lon, lat, lon)
                elevation = self._synthetic_elevation(cum)
                points.append(RoutePoint(lat=lat, lon=lon, elevation_m=elevation, cum_dist_km=cum))

        return RouteResult(points=points)

    @staticmethod
    def _synthetic_elevation(cum_km: float) -> float:
        # Base elevation plus two "passes" to simulate mountain terrain (loosely
        # modeled after a Manali -> Leh style profile: base ~2000m, peaks near
        # 4000-5000m at a couple of high passes). Purely for exercising the
        # engine's grade/altitude logic offline -- not real elevation data.
        base = 2000 + 300 * math.sin(cum_km / 60)
        pass1 = 2400 * math.exp(-((cum_km - 120) ** 2) / (2 * 25 ** 2))
        pass2 = 1800 * math.exp(-((cum_km - 350) ** 2) / (2 * 20 ** 2))
        return round(base + pass1 + pass2, 1)
