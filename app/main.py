from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.itinerary import router as itinerary_router
from app.config import get_settings
from app.db.mongodb import mongodb_lifespan

settings = get_settings()

app = FastAPI(
    title="RiderTrail API",
    description=(
        "Graph-based motorcycle trip itinerary engine -- route splitting, "
        "fuel-range planning, and weather/elevation overlays."
    ),
    version="0.1.0",
    lifespan=mongodb_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(itinerary_router)


@app.get("/")
async def root():
    return {"service": "RiderTrail API", "status": "running"}
