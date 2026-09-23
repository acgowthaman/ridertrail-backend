"""
Rider Constraint Engine + Graph-Based Itinerary Builder + Weather/Elevation Overlay.

This module is the core of RiderTrail. It takes a route (as a polyline with
elevation, from a RoutingProvider) and a rider's constraints, and walks the
route point-by-point, simultaneously:

  1. Tracking accumulated riding time (using a terrain- and grade-adjusted
     speed model) to decide where each day's ride should end.
  2. Tracking remaining fuel range to insert mandatory fuel stops before the
     tank would realistically run dry.
  3. Tracking elevation to flag high-altitude passes.
  4. Calling the WeatherProvider once per overnight stop to flag hazardous
     conditions.

`build_itinerary` is a pure(ish) async function of its inputs -- it holds no
state between calls. That's what makes "Dynamic Re-Calculation" simple: the
frontend just calls the /api/v1/itinerary endpoint again with the updated
waypoints/constraints and gets a freshly recomputed plan.
"""
from datetime import date, timedelta
from dataclasses import dataclass
from typing import List

from app.geo_utils import calculate_slope_percentage
from app.providers.base import RoutingProvider, RouteResult, WeatherProvider, WeatherSnapshot
from app.schemas import (
    Coordinate,
    DayPlan,
    FuelStop,
    ItineraryRequest,
    ItineraryResponse,
    TerrainPreference,
    TrailDifficulty,
    TrailDifficultyRating,
    WeatherWarning,
)

TERRAIN_BASE_SPEED_KMH = {
    TerrainPreference.HIGHWAY: 65.0,
    TerrainPreference.MIXED: 50.0,
    TerrainPreference.SCENIC: 38.0,
}

MIN_SPEED_FLOOR_KMH = 15.0
HIGH_ALTITUDE_WARNING_M = 3500.0
SEVERE_WEATHER_CONDITIONS = {"Thunderstorm", "Snow"}


@dataclass
class _GradeTracker:
    """Aggregates segment grades into the rating displayed to riders."""

    horizontal_distance_m: float = 0.0
    weighted_absolute_grade: float = 0.0
    maximum_grade_pct: float = 0.0
    maximum_uphill_grade_pct: float = 0.0
    maximum_downhill_grade_pct: float = 0.0

    def add(self, grade_pct: float, horizontal_distance_m: float) -> None:
        if horizontal_distance_m <= 0:
            return

        self.horizontal_distance_m += horizontal_distance_m
        self.weighted_absolute_grade += abs(grade_pct) * horizontal_distance_m
        self.maximum_grade_pct = max(self.maximum_grade_pct, abs(grade_pct))
        self.maximum_uphill_grade_pct = max(self.maximum_uphill_grade_pct, grade_pct)
        self.maximum_downhill_grade_pct = min(self.maximum_downhill_grade_pct, grade_pct)

    def rating(self) -> TrailDifficultyRating:
        average_grade_pct = (
            self.weighted_absolute_grade / self.horizontal_distance_m
            if self.horizontal_distance_m
            else 0.0
        )
        maximum_grade_pct = self.maximum_grade_pct

        if average_grade_pct <= 2 and maximum_grade_pct <= 5:
            difficulty = TrailDifficulty.EASY
        elif average_grade_pct <= 5 and maximum_grade_pct <= 10:
            difficulty = TrailDifficulty.MODERATE
        elif average_grade_pct <= 8 and maximum_grade_pct <= 15:
            difficulty = TrailDifficulty.CHALLENGING
        else:
            difficulty = TrailDifficulty.EXPERT

        return TrailDifficultyRating(
            average_grade_pct=round(average_grade_pct, 2),
            maximum_grade_pct=round(maximum_grade_pct, 2),
            maximum_uphill_grade_pct=round(self.maximum_uphill_grade_pct, 2),
            maximum_downhill_grade_pct=round(self.maximum_downhill_grade_pct, 2),
            rating=difficulty,
        )


def _local_speed_kmh(base_speed: float, grade_pct: float, elevation_m: float) -> float:
    """Effective speed for a short segment, penalized for steep grade and altitude."""
    speed = base_speed
    abs_grade = abs(grade_pct)

    if abs_grade > 8:
        speed *= 0.5
    elif abs_grade > 4:
        speed *= 0.7
    elif abs_grade > 2:
        speed *= 0.85

    if elevation_m > 4500:
        speed *= 0.55
    elif elevation_m > 3500:
        speed *= 0.75
    elif elevation_m > 2500:
        speed *= 0.9

    return max(speed, MIN_SPEED_FLOOR_KMH)


def _weather_warnings(weather: WeatherSnapshot, target_date: date, label: str) -> List[WeatherWarning]:
    severity = "info"
    if weather.condition in SEVERE_WEATHER_CONDITIONS or weather.precipitation_probability >= 0.6:
        severity = "danger"
    elif weather.condition == "Rain" or weather.precipitation_probability >= 0.3:
        severity = "warning"

    if severity == "info":
        return []

    return [
        WeatherWarning(
            date=target_date,
            location_label=label,
            condition=weather.condition,
            severity=severity,
            message=(
                f"{weather.condition} expected ({round(weather.precipitation_probability * 100)}% "
                f"precipitation chance, {round(weather.temp_c)}°C, {round(weather.wind_kmh)} km/h wind)."
            ),
        )
    ]


async def build_itinerary(
    request: ItineraryRequest,
    routing_provider: RoutingProvider,
    weather_provider: WeatherProvider,
) -> ItineraryResponse:
    constraints = request.constraints
    coordinates = [request.start, *request.waypoints, request.end]

    route: RouteResult = await routing_provider.get_route(coordinates)
    points = route.points
    if len(points) < 2:
        raise ValueError("Routing provider returned an unusable route (fewer than 2 points).")

    base_speed = TERRAIN_BASE_SPEED_KMH[constraints.terrain_preference]
    tank_range_km = constraints.fuel_tank_range_km
    reserve_km = tank_range_km * (constraints.fuel_reserve_percent / 100.0)
    usable_range_km = max(tank_range_km - reserve_km, 10.0)

    days: List[DayPlan] = []
    trip_warnings: List[str] = []
    seen_high_altitude_overall = False

    day_number = 1
    current_date = constraints.start_date
    day_start_idx = 0
    day_time_acc = 0.0
    day_dist_acc = 0.0
    fuel_since_last_stop_km = 0.0
    day_fuel_stops: List[FuelStop] = []
    day_max_elev = points[0].elevation_m
    day_elev_gain = 0.0
    high_altitude_hit = False
    day_grade_tracker = _GradeTracker()
    trip_grade_tracker = _GradeTracker()

    n = len(points)
    i = 1
    while i < n:
        p_prev, p_curr = points[i - 1], points[i]
        seg_dist = p_curr.cum_dist_km - p_prev.cum_dist_km
        if seg_dist <= 0:
            i += 1
            continue

        grade_pct = calculate_slope_percentage(
            (p_prev.lat, p_prev.lon),
            (p_curr.lat, p_curr.lon),
            p_prev.elevation_m,
            p_curr.elevation_m,
        )
        day_grade_tracker.add(grade_pct, seg_dist * 1000)
        trip_grade_tracker.add(grade_pct, seg_dist * 1000)
        speed = _local_speed_kmh(base_speed, grade_pct, p_curr.elevation_m)
        seg_time = seg_dist / speed

        day_time_acc += seg_time
        day_dist_acc += seg_dist
        fuel_since_last_stop_km += seg_dist

        if p_curr.elevation_m > day_max_elev:
            day_max_elev = p_curr.elevation_m
        elev_delta = p_curr.elevation_m - p_prev.elevation_m
        if elev_delta > 0:
            day_elev_gain += elev_delta

        if p_curr.elevation_m > HIGH_ALTITUDE_WARNING_M:
            high_altitude_hit = True
            seen_high_altitude_overall = True

        # Mandatory fuel stop: force one before the tank would run past the reserve.
        if fuel_since_last_stop_km >= usable_range_km and i < n - 1:
            day_fuel_stops.append(
                FuelStop(
                    location=Coordinate(lat=p_curr.lat, lon=p_curr.lon),
                    distance_from_start_km=round(p_curr.cum_dist_km, 1),
                    reason="mandatory_fuel",
                )
            )
            fuel_since_last_stop_km = 0.0

        day_ends_here = day_time_acc >= constraints.max_riding_hours_per_day or i == n - 1

        if day_ends_here:
            weather = await weather_provider.get_forecast(p_curr.lat, p_curr.lon, current_date)
            weather_warnings = _weather_warnings(weather, current_date, f"Day {day_number} stop")
            if high_altitude_hit:
                weather_warnings.append(
                    WeatherWarning(
                        date=current_date,
                        location_label=f"Day {day_number} stop",
                        condition="High Altitude",
                        severity="warning",
                        message=(
                            f"Route crosses {round(day_max_elev)}m today. Watch for altitude "
                            f"sickness symptoms and acclimatize before pushing further."
                        ),
                    )
                )

            days.append(
                DayPlan(
                    day_number=day_number,
                    date=current_date,
                    start_point=Coordinate(lat=points[day_start_idx].lat, lon=points[day_start_idx].lon),
                    end_point=Coordinate(lat=p_curr.lat, lon=p_curr.lon),
                    distance_km=round(day_dist_acc, 1),
                    estimated_riding_hours=round(day_time_acc, 2),
                    fuel_stops=day_fuel_stops,
                    elevation_gain_m=round(day_elev_gain, 1),
                    max_elevation_m=round(day_max_elev, 1),
                    weather=weather_warnings,
                    trail_difficulty=day_grade_tracker.rating(),
                )
            )

            # Reset day accumulators for the next riding day.
            day_start_idx = i
            day_time_acc = 0.0
            day_dist_acc = 0.0
            day_fuel_stops = []
            day_max_elev = p_curr.elevation_m
            day_elev_gain = 0.0
            high_altitude_hit = False
            day_grade_tracker = _GradeTracker()
            current_date = current_date + timedelta(days=1)

            if day_number in constraints.rest_after_days and i != n - 1:
                days.append(
                    DayPlan(
                        day_number=day_number + 1,
                        date=current_date,
                        start_point=Coordinate(lat=p_curr.lat, lon=p_curr.lon),
                        end_point=Coordinate(lat=p_curr.lat, lon=p_curr.lon),
                        distance_km=0.0,
                        estimated_riding_hours=0.0,
                        fuel_stops=[],
                        elevation_gain_m=0.0,
                        max_elevation_m=round(p_curr.elevation_m, 1),
                        weather=[],
                        is_rest_day=True,
                    )
                )
                current_date = current_date + timedelta(days=1)
                day_number += 1

            day_number += 1

        i += 1

    if seen_high_altitude_overall:
        trip_warnings.append(
            "This route crosses high-altitude terrain (>3500m). Confirm pass status "
            "(seasonal closures, landslides) shortly before departure."
        )

    total_distance_km = points[-1].cum_dist_km
    total_riding_hours = sum(d.estimated_riding_hours for d in days)
    fuel_cost = (total_distance_km / constraints.bike_mileage_kmpl) * constraints.fuel_price_per_liter

    return ItineraryResponse(
        total_distance_km=round(total_distance_km, 1),
        total_days=len(days),
        total_riding_hours=round(total_riding_hours, 2),
        estimated_fuel_cost=round(fuel_cost, 2),
        days=days,
        warnings=trip_warnings,
        trail_difficulty=trip_grade_tracker.rating(),
    )
