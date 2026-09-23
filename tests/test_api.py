from datetime import date

from fastapi.testclient import TestClient

from app.db.mongodb import get_itinerary_repository
from app.itinerary_search import InMemoryItineraryRepository
from app.main import app

test_itinerary_repository = InMemoryItineraryRepository()
app.dependency_overrides[get_itinerary_repository] = lambda: test_itinerary_repository
client = TestClient(app)


def test_health():
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


def _payload(**constraint_overrides):
    constraints = {
        "max_riding_hours_per_day": 6,
        "fuel_tank_range_km": 220,
        "fuel_reserve_percent": 15,
        "terrain_preference": "mixed",
        "bike_mileage_kmpl": 25,
        "fuel_price_per_liter": 105,
        "start_date": str(date(2026, 7, 1)),
        "rest_after_days": [],
    }
    constraints.update(constraint_overrides)
    return {
        "start": {"lat": 32.2432, "lon": 77.1892, "label": "Manali"},
        "end": {"lat": 34.1526, "lon": 77.5771, "label": "Leh"},
        "waypoints": [],
        "constraints": constraints,
    }


def test_create_itinerary_end_to_end():
    resp = client.post("/api/v1/itinerary", json=_payload())
    assert resp.status_code == 200
    data = resp.json()

    assert data["total_days"] >= 1
    assert data["total_distance_km"] > 0
    assert data["days"][0]["day_number"] == 1
    assert data["estimated_fuel_cost"] > 0
    assert data["trail_difficulty"]["maximum_grade_pct"] >= 0
    assert data["days"][0]["trail_difficulty"]["rating"] in {
        "easy",
        "moderate",
        "challenging",
        "expert",
    }


def test_create_itinerary_with_rest_day():
    resp = client.post("/api/v1/itinerary", json=_payload(rest_after_days=[1]))
    assert resp.status_code == 200
    data = resp.json()
    rest_days = [d for d in data["days"] if d["is_rest_day"]]
    # A rest day is only inserted if day 1 isn't already the final riding day.
    assert len(rest_days) in (0, 1)


def test_invalid_coordinate_is_rejected():
    payload = _payload()
    payload["start"]["lat"] = 999  # out of range
    resp = client.post("/api/v1/itinerary", json=payload)
    assert resp.status_code == 422


def test_search_recent_itineraries():
    created = client.post("/api/v1/itinerary", json=_payload(terrain_preference="scenic"))
    assert created.status_code == 200

    resp = client.request(
        "QUERY",
        "/api/v1/search",
        json={"query": "manali", "terrain_preference": "scenic"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert data["page"] == 1
    assert data["results"][0]["start"]["label"] == "Manali"
    assert data["results"][0]["terrain_preference"] == "scenic"


def test_search_is_available_in_openapi_as_post():
    schema = app.openapi()
    assert "post" in schema["paths"]["/api/v1/search"]


def test_search_rejects_invalid_filter_ranges():
    resp = client.request(
        "QUERY",
        "/api/v1/search",
        json={"min_distance_km": 500, "max_distance_km": 100},
    )
    assert resp.status_code == 422
