"""Health check route."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    sim_time: float
    version: str


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Return service health. Always 200 if the backend is running."""
    return HealthResponse(status="ok", sim_time=0.0, version="0.1.0")
