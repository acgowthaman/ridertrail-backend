"""
Phase-2 scaffold: persistence layer (PostgreSQL + PostGIS).

NOT wired up yet. The itinerary engine (app/engine/itinerary_engine.py) is
currently stateless -- call the API again with new inputs to "recalculate".
These models sketch out what Trip / Stop / POI persistence will look like once
we add:
  - saving generated itineraries (Trip + Stop rows)
  - POI search ("fuel stations / rider-friendly stays within Xkm of this point")
    via PostGIS ST_DWithin
  - route history for a logged-in rider

Requires: sqlalchemy, geoalchemy2, psycopg2-binary, and a running Postgres
instance with the postgis extension enabled (see docker-compose.yml).
"""
from datetime import date

from geoalchemy2 import Geography
from sqlalchemy import Boolean, Column, Date, Float, ForeignKey, Integer, String
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Trip(Base):
    __tablename__ = "trips"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    start_date = Column(Date, nullable=False)
    total_distance_km = Column(Float)
    total_days = Column(Integer)
    created_at = Column(Date, default=date.today)

    stops = relationship("Stop", back_populates="trip", cascade="all, delete-orphan")


class Stop(Base):
    __tablename__ = "stops"

    id = Column(Integer, primary_key=True)
    trip_id = Column(Integer, ForeignKey("trips.id"), nullable=False)
    day_number = Column(Integer, nullable=False)
    stop_type = Column(String, nullable=False)  # "overnight" | "fuel" | "rest"
    label = Column(String)
    location = Column(Geography(geometry_type="POINT", srid=4326), nullable=False)
    is_rest_day = Column(Boolean, default=False)

    trip = relationship("Trip", back_populates="stops")


class PointOfInterest(Base):
    """Fuel stations, rider-friendly stays, viewpoints, etc. Queried with
    PostGIS ST_DWithin for "find all X within 10km of this point" lookups."""

    __tablename__ = "points_of_interest"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)  # "fuel_station" | "stay" | "viewpoint" | ...
    location = Column(Geography(geometry_type="POINT", srid=4326), nullable=False)
    rating = Column(Float, nullable=True)
