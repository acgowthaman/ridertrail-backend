from datetime import date

import pytest

from app.engine.itinerary_engine import build_itinerary
from app.geo_utils import calculate_slope_percentage, haversine_km
from app.providers.routing import MockRoutingProvider
from app.providers.weather import MockWeatherProvider
from app.schemas import Coordinate, ItineraryRequest, RiderConstraints, TerrainPreference

# Deliberately far apart so multi-day splitting, mandatory fuel stops, and the
# synthetic high-altitude "passes" baked into MockRoutingProvider are all
# exercised deterministically, regardless of exact speed/grade tuning.
FAR_START = Coordinate(lat=8.0883, lon=77.5385, label="Kanyakumari (test)")
FAR_END = Coordinate(lat=34.1526, lon=77.5771, label="Leh (test)")

# Realistic-flavor coordinates matching the actual product use case.
MANALI = Coordinate(lat=32.2432, lon=77.1892, label="Manali")
LEH = Coordinate(lat=34.1526, lon=77.5771, label="Leh")


def _constraints(**overrides) -> RiderConstraints:
    defaults = dict(
        max_riding_hours_per_day=6,
        fuel_tank_range_km=220,
        fuel_reserve_percent=15,
        terrain_preference=TerrainPreference.MIXED,
        bike_mileage_kmpl=25,
        fuel_price_per_liter=105,
        start_date=date(2026, 7, 1),
        rest_after_days=[],
    )
    defaults.update(overrides)
    return RiderConstraints(**defaults)


async def _build(start, end, **constraint_overrides):
    req = ItineraryRequest(start=start, end=end, waypoints=[], constraints=_constraints(**constraint_overrides))
    return await build_itinerary(req, MockRoutingProvider(), MockWeatherProvider())


async def test_route_distance_matches_geodesic_for_direct_leg():
    result = await _build(FAR_START, FAR_END)
    expected_km = haversine_km(FAR_START.lat, FAR_START.lon, FAR_END.lat, FAR_END.lon)
    assert result.total_distance_km == pytest.approx(expected_km, rel=0.02)


def test_calculate_slope_percentage_returns_signed_grade():
    # 0.0089932° longitude at the equator is approximately one horizontal km.
    start = (0.0, 0.0)
    end = (0.0, 0.0089932)

    assert calculate_slope_percentage(start, end, 100, 150) == pytest.approx(5.0, abs=0.01)
    assert calculate_slope_percentage(end, start, 150, 100) == pytest.approx(-5.0, abs=0.01)
    assert calculate_slope_percentage(start, start, 100, 150) == 0.0


async def test_long_trip_splits_into_multiple_days():
    result = await _build(FAR_START, FAR_END)
    assert result.total_days >= 5  # ~2900km, well under 300km/day capacity
    day_dist_sum = sum(d.distance_km for d in result.days)
    assert day_dist_sum == pytest.approx(result.total_distance_km, rel=0.02)


async def test_day_numbers_and_dates_are_sequential():
    result = await _build(FAR_START, FAR_END)
    for idx, day in enumerate(result.days, start=1):
        assert day.day_number == idx
    assert result.days[0].date == date(2026, 7, 1)


async def test_mandatory_fuel_stops_inserted_on_long_trip():
    result = await _build(FAR_START, FAR_END)
    total_fuel_stops = sum(len(d.fuel_stops) for d in result.days)
    # ~2900km trip / ~187km usable range (220km tank - 15% reserve) => many stops
    assert total_fuel_stops >= 10


async def test_smaller_tank_forces_more_fuel_stops():
    big_tank = await _build(FAR_START, FAR_END, fuel_tank_range_km=400)
    small_tank = await _build(FAR_START, FAR_END, fuel_tank_range_km=150)

    big_tank_stops = sum(len(d.fuel_stops) for d in big_tank.days)
    small_tank_stops = sum(len(d.fuel_stops) for d in small_tank.days)
    assert small_tank_stops > big_tank_stops


async def test_rest_day_adds_exactly_one_day():
    plain = await _build(FAR_START, FAR_END)
    with_rest = await _build(FAR_START, FAR_END, rest_after_days=[2])

    assert with_rest.total_days == plain.total_days + 1
    rest_days = [d for d in with_rest.days if d.is_rest_day]
    assert len(rest_days) == 1
    assert rest_days[0].distance_km == 0.0
    assert rest_days[0].estimated_riding_hours == 0.0


async def test_high_altitude_warning_present():
    result = await _build(FAR_START, FAR_END)
    assert any("high-altitude" in w.lower() for w in result.warnings)


async def test_trail_difficulty_exposes_grade_metrics():
    result = await _build(FAR_START, FAR_END)

    rating = result.trail_difficulty
    assert rating.average_grade_pct > 0
    assert rating.maximum_grade_pct >= rating.average_grade_pct
    assert rating.maximum_uphill_grade_pct > 0
    assert rating.maximum_downhill_grade_pct < 0
    assert rating.rating in {"easy", "moderate", "challenging", "expert"}
    assert all(day.trail_difficulty.maximum_grade_pct >= 0 for day in result.days)


async def test_scenic_terrain_is_slower_than_highway():
    scenic = await _build(FAR_START, FAR_END, terrain_preference=TerrainPreference.SCENIC)
    highway = await _build(FAR_START, FAR_END, terrain_preference=TerrainPreference.HIGHWAY)
    assert scenic.total_riding_hours > highway.total_riding_hours
    assert scenic.total_days >= highway.total_days


async def test_fuel_cost_scales_with_distance_and_price():
    result = await _build(FAR_START, FAR_END, bike_mileage_kmpl=25, fuel_price_per_liter=100)
    expected_cost = (result.total_distance_km / 25) * 100
    assert result.estimated_fuel_cost == pytest.approx(expected_cost, rel=0.01)


async def test_manali_leh_smoke():
    """Realistic-flavor smoke test for the actual RiderTrail use case."""
    result = await _build(MANALI, LEH)
    assert result.total_days >= 1
    assert result.total_distance_km > 0
    assert result.days[0].day_number == 1
    assert result.estimated_fuel_cost > 0
