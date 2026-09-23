"""MongoDB lifecycle and repository dependency for the RiderTrail API."""
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any

from fastapi import FastAPI, Request
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import get_settings
from app.itinerary_search import ItineraryRepository, MongoItineraryRepository


@asynccontextmanager
async def mongodb_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open one shared async client at startup and close it at shutdown."""
    settings = get_settings()
    client: AsyncMongoClient[Dict[str, Any]] = AsyncMongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
        tz_aware=True,
    )
    database: AsyncDatabase[Dict[str, Any]] = client.get_database(settings.mongodb_database)
    repository = MongoItineraryRepository(database.get_collection("itineraries"))

    # PyMongo connects lazily; ping now makes a missing local MongoDB instance
    # fail clearly during startup instead of on the first itinerary request.
    try:
        await client.admin.command({"ping": 1})
        await repository.ensure_indexes()
    except Exception:
        await client.close()
        raise

    app.state.itinerary_repository = repository
    try:
        yield
    finally:
        await client.close()


def get_itinerary_repository(request: Request) -> ItineraryRepository:
    return request.app.state.itinerary_repository
