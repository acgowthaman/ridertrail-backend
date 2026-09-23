from datetime import date, datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TerrainPreference(str, Enum):
    HIGHWAY = "highway"
    MIXED = "mixed"
    SCENIC = "scenic"  # twisties / ghat roads


class Coordinate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    label: Optional[str] = None


class RiderConstraints(BaseModel):
    max_riding_hours_per_day: float = Field(
        6, gt=0, le=16, description="Rider's self-imposed daily riding cap, in hours."
    )
    fuel_tank_range_km: float = Field(220, gt=0, description="Real-world range on a full tank, in km.")
    fuel_reserve_percent: float = Field(
        15, ge=0, le=50, description="Safety buffer kept in the tank before a fuel stop is forced."
    )
    terrain_preference: TerrainPreference = TerrainPreference.MIXED
    bike_mileage_kmpl: float = Field(25, gt=0, description="Fuel efficiency, km per liter, for cost estimation.")
    fuel_price_per_liter: float = Field(105.0, gt=0)
    start_date: date
    rest_after_days: List[int] = Field(
        default_factory=list,
        description="1-based riding-day numbers after which a full rest day is inserted.",
    )


class ItineraryRequest(BaseModel):
    start: Coordinate
    end: Coordinate
    waypoints: List[Coordinate] = Field(default_factory=list, description="Ordered mandatory intermediate stops.")
    constraints: RiderConstraints


class FuelStop(BaseModel):
    location: Coordinate
    distance_from_start_km: float
    reason: str = "mandatory_fuel"


class WeatherWarning(BaseModel):
    date: date
    location_label: str
    condition: str
    severity: str  # "info" | "warning" | "danger"
    message: str


class TrailDifficulty(str, Enum):
    EASY = "easy"
    MODERATE = "moderate"
    CHALLENGING = "challenging"
    EXPERT = "expert"


class TrailDifficultyRating(BaseModel):
    """Grade-based difficulty metrics calculated from the routed elevation profile."""

    average_grade_pct: float = Field(
        default=0.0,
        description="Distance-weighted average of absolute segment grades; downhill counts as steepness.",
    )
    maximum_grade_pct: float = Field(
        default=0.0,
        description="Largest absolute segment grade encountered on the route.",
    )
    maximum_uphill_grade_pct: float = Field(
        default=0.0,
        description="Steepest uphill segment grade.",
    )
    maximum_downhill_grade_pct: float = Field(
        default=0.0,
        description="Steepest downhill segment grade (negative); zero means no descent.",
    )
    rating: TrailDifficulty = TrailDifficulty.EASY


class DayPlan(BaseModel):
    day_number: int
    date: date
    start_point: Coordinate
    end_point: Coordinate
    distance_km: float
    estimated_riding_hours: float
    fuel_stops: List[FuelStop] = Field(default_factory=list)
    elevation_gain_m: float = 0.0
    max_elevation_m: float = 0.0
    weather: List[WeatherWarning] = Field(default_factory=list)
    is_rest_day: bool = False
    trail_difficulty: TrailDifficultyRating = Field(default_factory=TrailDifficultyRating)


class ItineraryResponse(BaseModel):
    total_distance_km: float
    total_days: int
    total_riding_hours: float
    estimated_fuel_cost: float
    days: List[DayPlan]
    warnings: List[str] = Field(default_factory=list)
    trail_difficulty: TrailDifficultyRating = Field(default_factory=TrailDifficultyRating)


class FilterSchema(BaseModel):
    """Filters accepted by the recent-itinerary search endpoint."""

    model_config = ConfigDict(extra="forbid")

    query: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Case-insensitive text to match against start, end, and waypoint labels.",
    )
    terrain_preference: Optional[TerrainPreference] = None
    start_date_from: Optional[date] = None
    start_date_to: Optional[date] = None
    min_distance_km: Optional[float] = Field(default=None, ge=0)
    max_distance_km: Optional[float] = Field(default=None, ge=0)
    min_total_days: Optional[int] = Field(default=None, ge=1)
    max_total_days: Optional[int] = Field(default=None, ge=1)
    warnings_only: bool = False
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_ranges(self) -> "FilterSchema":
        if (
            self.start_date_from is not None
            and self.start_date_to is not None
            and self.start_date_from > self.start_date_to
        ):
            raise ValueError("start_date_from must be on or before start_date_to")
        if (
            self.min_distance_km is not None
            and self.max_distance_km is not None
            and self.min_distance_km > self.max_distance_km
        ):
            raise ValueError("min_distance_km must not exceed max_distance_km")
        if (
            self.min_total_days is not None
            and self.max_total_days is not None
            and self.min_total_days > self.max_total_days
        ):
            raise ValueError("min_total_days must not exceed max_total_days")
        return self


class ItinerarySearchResult(BaseModel):
    """A compact representation of an itinerary returned by search."""

    id: UUID
    created_at: datetime
    start: Coordinate
    end: Coordinate
    waypoint_count: int
    terrain_preference: TerrainPreference
    start_date: date
    total_distance_km: float
    total_days: int
    total_riding_hours: float
    estimated_fuel_cost: float
    warnings: List[str] = Field(default_factory=list)


class ItinerarySearchResponse(BaseModel):
    total: int
    page: int
    page_size: int
    results: List[ItinerarySearchResult]
