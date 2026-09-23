from fastapi import APIRouter, Depends

from app.db.mongodb import get_itinerary_repository
from app.dependencies import get_routing_provider, get_weather_provider
from app.engine.itinerary_engine import build_itinerary
from app.itinerary_search import ItineraryRepository
from app.providers.base import RoutingProvider, WeatherProvider
from app.schemas import (
    FilterSchema,
    ItineraryRequest,
    ItineraryResponse,
    ItinerarySearchResponse,
)

router = APIRouter(prefix="/api/v1", tags=["itinerary"])


@router.post("/itinerary", response_model=ItineraryResponse)
async def create_itinerary(
    request: ItineraryRequest,
    routing_provider: RoutingProvider = Depends(get_routing_provider),
    weather_provider: WeatherProvider = Depends(get_weather_provider),
    itinerary_repository: ItineraryRepository = Depends(get_itinerary_repository),
) -> ItineraryResponse:
    """
    Build (or rebuild) a full day-by-day itinerary for the given start/end/
    waypoints and rider constraints.

    This endpoint is deliberately stateless and idempotent: the frontend can
    call it again any time a waypoint is dragged, a rest day is added, or a
    constraint changes, and get back a freshly recalculated itinerary --
    that's the "Dynamic Re-Calculation" feature from the product spec.
    """
    itinerary = await build_itinerary(request, routing_provider, weather_provider)
    await itinerary_repository.add(request, itinerary)
    return itinerary


@router.get("/health")
async def health():
    return {"status": "ok"}

@router.post(
    "/search",
    response_model=ItinerarySearchResponse,
    summary="Search recently generated itineraries",
)
@router.api_route(
    "/search",
    methods=["QUERY"],
    response_model=ItinerarySearchResponse,
    include_in_schema=False,
)
async def filter_itineraries(
    filters: FilterSchema,
    itinerary_repository: ItineraryRepository = Depends(get_itinerary_repository),
) -> ItinerarySearchResponse:
    """Search the MongoDB collection of generated itineraries.

    Both HTTP ``QUERY`` and the documented ``POST`` compatibility route carry
    the structured filter object in the request body.
    """
    total, results = await itinerary_repository.search(filters)
    return ItinerarySearchResponse(
        total=total,
        page=filters.page,
        page_size=filters.page_size,
        results=results,
    )
