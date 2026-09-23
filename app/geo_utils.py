"""Geospatial helper functions used across the routing & itinerary engine."""
import math
from typing import List, Tuple

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def calculate_slope_percentage(
    coord1: Tuple[float, float],
    coord2: Tuple[float, float],
    elevation1_m: float,
    elevation2_m: float,
) -> float:
    """Return the signed grade between two elevated coordinates as a percentage.

    ``coord1`` and ``coord2`` are ``(latitude, longitude)`` pairs, and elevation
    values are metres. Positive grades are uphill; negative grades are downhill.
    For the closely spaced points in a route polyline, a local tangent-plane
    projection gives the horizontal north/south and east/west distances. Their
    Pythagorean resultant (``math.hypot``) is the horizontal run. ``math.atan``
    converts rise/run into the physical climb angle before converting it back
    to a percentage grade.
    """
    lat1_rad = math.radians(coord1[0])
    lat2_rad = math.radians(coord2[0])
    lon1_rad = math.radians(coord1[1])
    lon2_rad = math.radians(coord2[1])
    earth_radius_m = EARTH_RADIUS_KM * 1000
    north_south_distance_m = earth_radius_m * (lat2_rad - lat1_rad)
    east_west_distance_m = earth_radius_m * math.cos((lat1_rad + lat2_rad) / 2) * (lon2_rad - lon1_rad)
    horizontal_distance_m = math.hypot(north_south_distance_m, east_west_distance_m)
    if horizontal_distance_m == 0:
        return 0.0

    elevation_change_m = elevation2_m - elevation1_m
    slope_angle_rad = math.atan(elevation_change_m / horizontal_distance_m)
    grade_percentage = math.tan(slope_angle_rad) * 100
    return round(grade_percentage, 2)


def cumulative_distances(points: List[Tuple[float, float]]) -> List[float]:
    """Given [(lat, lon), ...] return cumulative distance in km at each point, starting at 0."""
    cum = [0.0]
    for i in range(1, len(points)):
        lat1, lon1 = points[i - 1]
        lat2, lon2 = points[i]
        cum.append(cum[-1] + haversine_km(lat1, lon1, lat2, lon2))
    return cum
