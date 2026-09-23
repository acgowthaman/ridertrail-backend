# RiderTrail — Backend (v1)

A graph-based itinerary engine for motorcycle touring — "Pickyourtrail for bike trips."
This first slice covers the compute pipeline described in the spec: **get rider
inputs → call routing/weather APIs → return a realistic day-by-day itinerary.**
Generated itineraries are saved in MongoDB, so their search results persist
across API restarts. The separate PostGIS scaffold remains reserved for future
geospatial POI queries — see [Roadmap](#roadmap).

## What's implemented

| Module (from the spec)              | Where |
|--------------------------------------|-------|
| Rider Constraint Engine              | `app/schemas.py` (`RiderConstraints`) |
| Graph-Based Itinerary Builder        | `app/engine/itinerary_engine.py` |
| Dynamic Re-Calculation               | The `/api/v1/itinerary` endpoint is stateless — call it again with new inputs |
| Live Weather & Elevation Overlay     | `app/providers/weather.py` + altitude logic in the engine |

## Architecture

```
Client (curl / future React frontend)
        │  POST /api/v1/itinerary
        ▼
FastAPI app (app/main.py, app/api/itinerary.py)
        │
        ▼
build_itinerary()  ── app/engine/itinerary_engine.py
        │                 │
        ▼                 ▼
RoutingProvider     WeatherProvider
(app/providers)     (app/providers)
        │                 │
  OpenRouteService   OpenWeatherMap
  (or MockRouting-    (or MockWeather-
   Provider if no      Provider if no
   ORS_API_KEY)        OWM_API_KEY)
```

**Provider abstraction:** `RoutingProvider` and `WeatherProvider` are abstract
base classes (`app/providers/base.py`) with a real implementation and a
deterministic offline mock. If `ORS_API_KEY` / `OWM_API_KEY` aren't set, the
app automatically falls back to the mocks — so you can run, demo, and test the
whole thing with zero external API keys. This is also what makes the test
suite hermetic (no network calls, no flakiness).

## How the engine works

`build_itinerary()` walks the route's polyline point by point, tracking three
things simultaneously:

1. **Riding time** — a base speed per terrain preference (highway/mixed/scenic)
   is penalized for steep grade (computed from consecutive elevation points)
   and for high altitude. A day ends once accumulated time hits
   `max_riding_hours_per_day`.
2. **Fuel range** — distance since the last fuel stop is tracked against
   `fuel_tank_range_km` minus a safety reserve. Crossing that threshold inserts
   a mandatory fuel stop and resets the counter.
3. **Elevation** — crossing 3500m flags a high-altitude warning for that day
   and the trip overall (passes, altitude sickness risk).

At each day boundary the engine calls the weather provider once for that
stop's location/date and attaches any hazard warnings (rain, storms, snow).

It also calculates every routed elevation segment's signed grade
(`rise / horizontal run × 100`). The itinerary and each riding day return a
`trail_difficulty` object with a distance-weighted average absolute grade,
maximum grade, maximum uphill/downhill grades, and an `easy`, `moderate`,
`challenging`, or `expert` rating. The rating thresholds are: easy (≤2% average
and ≤5% max), moderate (≤5% / ≤10%), challenging (≤8% / ≤15%), then expert.

Because the whole thing is a pure function of `(request, providers)`, "dynamic
recalculation" is just calling the endpoint again with an updated waypoint,
rest day, or constraint — no server-side session state to manage in v1.

## Setup

```bash
cd ridertrail-backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # includes pytest
cp .env.example .env                  # leave keys blank to run in mock mode
```

## Run it

```bash
uvicorn app.main:app --reload
# → http://127.0.0.1:8000/docs for interactive Swagger UI
```

### Example request

```bash
curl -X POST http://127.0.0.1:8000/api/v1/itinerary \
  -H "Content-Type: application/json" \
  -d '{
    "start": {"lat": 32.2432, "lon": 77.1892, "label": "Manali"},
    "end": {"lat": 34.1526, "lon": 77.5771, "label": "Leh"},
    "waypoints": [],
    "constraints": {
      "max_riding_hours_per_day": 6,
      "fuel_tank_range_km": 220,
      "fuel_reserve_percent": 15,
      "terrain_preference": "scenic",
      "bike_mileage_kmpl": 25,
      "fuel_price_per_liter": 105,
      "start_date": "2026-07-01",
      "rest_after_days": [1]
    }
  }'
```

Returns a `total_distance_km`, `total_days`, `total_riding_hours`,
`estimated_fuel_cost`, and a `days[]` array — each with its distance, riding
hours, fuel stops, elevation stats, weather warnings, and whether it's a rest
day.

### Search recent itineraries

`POST /api/v1/search` (also available as `QUERY /api/v1/search`) searches
itineraries saved in MongoDB. Use `POST` for Swagger UI compatibility. Send
the filters as a JSON body; for example, to find recently generated scenic
trips that start in Manali:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query":"manali","terrain_preference":"scenic","page":1,"page_size":20}'
```

Available filters include `query`, `terrain_preference`, start-date and
distance/day ranges, and `warnings_only`. Results are newest first and include
pagination metadata.

### MongoDB

The API connects to `mongodb://127.0.0.1:27017/` and stores data in the
`RiderTrail.itineraries` collection. Start a local database with:

```bash
docker compose up -d mongo
```

Set `MONGODB_URI` and `MONGODB_DATABASE` in `.env` to use a different
deployment or database.

### Using real APIs instead of mocks

1. Get a free key from [openrouteservice.org](https://openrouteservice.org/dev/#/signup)
   and [openweathermap.org](https://openweathermap.org/api).
2. Put them in `.env` as `ORS_API_KEY` / `OWM_API_KEY`.
3. Restart the server — `app/dependencies.py` picks the real provider automatically
   whenever a key is present.

Note: ORS has no dedicated motorcycle profile, so `driving-car` is used as the
closest approximation (documented in `app/providers/routing.py`).

## Tests

```bash
pytest -v
```

15 tests, all running against the mock providers (no network required):
route-distance sanity checks, multi-day splitting, mandatory fuel-stop
insertion (and that a smaller tank forces more stops), rest-day insertion,
high-altitude warning triggering, terrain-speed differences, fuel-cost math,
and full API round-trips including a validation-error case.

## Design decisions worth knowing for a walkthrough

- **Motorcycle-specific inputs**: fuel tank range + reserve %, bike mileage
  (km/L) for cost, and a terrain preference that actually changes the speed
  model — these are the things that differentiate this from a generic
  car-trip planner like the PYT itself targets.
- **Grade-aware speed, not just distance/speed**: splitting purely on straight
  km/day would be unrealistic for Himalayan passes. Speed is recomputed per
  route segment from local grade and elevation, so a mountainous day
  realistically takes longer than a highway day of the same length.
- **Mock providers aren't just stubs** — `MockRoutingProvider` generates a
  synthetic elevation profile with actual "passes," so the altitude-warning
  and grade-penalty logic is exercised even completely offline.

## Roadmap

- [ ] Wire up `app/db/models.py` (PostGIS) for saved trips + POI search
      ("fuel stations / stays within 10km of this stop") — `docker-compose.yml`
      spins up Postgres+PostGIS for this.
      - Would swap the current placeholder fuel-stop points for real,
      geocoded fuel-station POIs instead of a bare lat/lon.
- [ ] Replace the linear day-splitting walk with a true graph search (Dijkstra
      over candidate stop nodes) once POIs exist, so day-ends snap to actual
      towns/stays rather than an arbitrary point on the polyline.
- [ ] `/api/v1/itinerary/recalculate` endpoint that patches a saved trip
      instead of a full stateless resend, once trips are persisted.
- [ ] Auth + per-rider trip history.
- [ ] React + Mapbox GL frontend consuming this API (drag-and-drop waypoints
      triggering re-POSTs to this endpoint).
# ridertrail-backend
