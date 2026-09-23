"""Repositories used to save and search generated itineraries."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from threading import RLock
from typing import Any, Deque, Dict, List, Protocol, Tuple
from uuid import UUID, uuid4

from pymongo.asynchronous.collection import AsyncCollection

from app.schemas import (
    FilterSchema,
    ItineraryRequest,
    ItineraryResponse,
    ItinerarySearchResult,
)


@dataclass(frozen=True)
class StoredItinerary:
    id: UUID
    created_at: datetime
    request: ItineraryRequest
    itinerary: ItineraryResponse


class ItineraryRepository(Protocol):
    async def add(self, request: ItineraryRequest, itinerary: ItineraryResponse) -> UUID:
        """Persist a generated itinerary and return its identifier."""

    async def search(self, filters: FilterSchema) -> Tuple[int, List[ItinerarySearchResult]]:
        """Return a page of itinerary summaries matching ``filters``."""


class InMemoryItineraryRepository:
    """Thread-safe, bounded repository used only by tests."""

    def __init__(self, max_records: int = 200) -> None:
        self._records: Deque[StoredItinerary] = deque(maxlen=max_records)
        self._lock = RLock()

    async def add(self, request: ItineraryRequest, itinerary: ItineraryResponse) -> UUID:
        record = StoredItinerary(
            id=uuid4(),
            created_at=datetime.now(timezone.utc),
            request=request.model_copy(deep=True),
            itinerary=itinerary.model_copy(deep=True),
        )
        with self._lock:
            self._records.append(record)
        return record.id

    async def search(self, filters: FilterSchema) -> Tuple[int, List[ItinerarySearchResult]]:
        # Copy while locked, then do the filtering outside the critical section.
        with self._lock:
            records = list(reversed(self._records))

        matches = [record for record in records if self._matches(record, filters)]
        total = len(matches)
        offset = (filters.page - 1) * filters.page_size
        page = matches[offset : offset + filters.page_size]
        return total, [self._to_result(record) for record in page]

    @staticmethod
    def _matches(record: StoredItinerary, filters: FilterSchema) -> bool:
        request = record.request
        itinerary = record.itinerary
        constraints = request.constraints

        if filters.query:
            labels = [request.start.label or "", request.end.label or ""]
            labels.extend(waypoint.label or "" for waypoint in request.waypoints)
            if filters.query.casefold() not in " ".join(labels).casefold():
                return False

        if (
            filters.terrain_preference is not None
            and constraints.terrain_preference != filters.terrain_preference
        ):
            return False
        if filters.start_date_from is not None and constraints.start_date < filters.start_date_from:
            return False
        if filters.start_date_to is not None and constraints.start_date > filters.start_date_to:
            return False
        if filters.min_distance_km is not None and itinerary.total_distance_km < filters.min_distance_km:
            return False
        if filters.max_distance_km is not None and itinerary.total_distance_km > filters.max_distance_km:
            return False
        if filters.min_total_days is not None and itinerary.total_days < filters.min_total_days:
            return False
        if filters.max_total_days is not None and itinerary.total_days > filters.max_total_days:
            return False
        if filters.warnings_only and not itinerary.warnings:
            return False
        return True

    @staticmethod
    def _to_result(record: StoredItinerary) -> ItinerarySearchResult:
        request = record.request
        itinerary = record.itinerary
        return ItinerarySearchResult(
            id=record.id,
            created_at=record.created_at,
            start=request.start,
            end=request.end,
            waypoint_count=len(request.waypoints),
            terrain_preference=request.constraints.terrain_preference,
            start_date=request.constraints.start_date,
            total_distance_km=itinerary.total_distance_km,
            total_days=itinerary.total_days,
            total_riding_hours=itinerary.total_riding_hours,
            estimated_fuel_cost=itinerary.estimated_fuel_cost,
            warnings=itinerary.warnings,
        )


class MongoItineraryRepository:
    """MongoDB-backed itinerary repository used by the API."""

    def __init__(self, collection: AsyncCollection[Dict[str, Any]]) -> None:
        self._collection = collection

    async def ensure_indexes(self) -> None:
        await self._collection.create_index([("created_at", -1)])
        await self._collection.create_index([("request.constraints.start_date", 1)])
        await self._collection.create_index([("request.constraints.terrain_preference", 1)])

    async def add(self, request: ItineraryRequest, itinerary: ItineraryResponse) -> UUID:
        itinerary_id = uuid4()
        await self._collection.insert_one(
            {
                "_id": str(itinerary_id),
                "created_at": datetime.now(timezone.utc),
                "request": request.model_dump(mode="json"),
                "itinerary": itinerary.model_dump(mode="json"),
            }
        )
        return itinerary_id

    async def search(self, filters: FilterSchema) -> Tuple[int, List[ItinerarySearchResult]]:
        query = self._build_query(filters)
        total = await self._collection.count_documents(query)
        offset = (filters.page - 1) * filters.page_size
        cursor = (
            self._collection.find(query)
            .sort("created_at", -1)
            .skip(offset)
            .limit(filters.page_size)
        )
        documents = await cursor.to_list(length=filters.page_size)
        return total, [self._to_result(document) for document in documents]

    @staticmethod
    def _build_query(filters: FilterSchema) -> Dict[str, Any]:
        query: Dict[str, Any] = {}

        if filters.query:
            label_pattern = {"$regex": re.escape(filters.query), "$options": "i"}
            query["$or"] = [
                {"request.start.label": label_pattern},
                {"request.end.label": label_pattern},
                {"request.waypoints.label": label_pattern},
            ]
        if filters.terrain_preference is not None:
            query["request.constraints.terrain_preference"] = filters.terrain_preference.value

        start_date: Dict[str, str] = {}
        if filters.start_date_from is not None:
            start_date["$gte"] = filters.start_date_from.isoformat()
        if filters.start_date_to is not None:
            start_date["$lte"] = filters.start_date_to.isoformat()
        if start_date:
            query["request.constraints.start_date"] = start_date

        distance: Dict[str, float] = {}
        if filters.min_distance_km is not None:
            distance["$gte"] = filters.min_distance_km
        if filters.max_distance_km is not None:
            distance["$lte"] = filters.max_distance_km
        if distance:
            query["itinerary.total_distance_km"] = distance

        total_days: Dict[str, int] = {}
        if filters.min_total_days is not None:
            total_days["$gte"] = filters.min_total_days
        if filters.max_total_days is not None:
            total_days["$lte"] = filters.max_total_days
        if total_days:
            query["itinerary.total_days"] = total_days

        if filters.warnings_only:
            query["itinerary.warnings.0"] = {"$exists": True}
        return query

    @staticmethod
    def _to_result(document: Dict[str, Any]) -> ItinerarySearchResult:
        request = ItineraryRequest.model_validate(document["request"])
        itinerary = ItineraryResponse.model_validate(document["itinerary"])
        created_at = document["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        return ItinerarySearchResult(
            id=UUID(document["_id"]),
            created_at=created_at,
            start=request.start,
            end=request.end,
            waypoint_count=len(request.waypoints),
            terrain_preference=request.constraints.terrain_preference,
            start_date=request.constraints.start_date,
            total_distance_km=itinerary.total_distance_km,
            total_days=itinerary.total_days,
            total_riding_hours=itinerary.total_riding_hours,
            estimated_fuel_cost=itinerary.estimated_fuel_cost,
            warnings=itinerary.warnings,
        )
