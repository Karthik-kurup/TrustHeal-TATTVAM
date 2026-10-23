"""
TrustHeal FastAPI application factory.

Wires together:
  - SimLoop (started in lifespan)
  - WebSocket hub
  - All REST routers
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.ws import hub
from app.api.health import router as health_router
from app.api.sim import router as sim_router
from app.api.cluster import router as cluster_router
from app.api.metrics import router as metrics_router
from app.api.incidents import router as incidents_router
from app.api.actions import router as actions_router
from app.api.control import router as control_router
from app.api.faults import router as faults_router
from app.api.scenarios import router as scenarios_router
from app.api.audit import router as audit_router
from app.api.models_report import router as models_router
from app.api.compare import router as compare_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start SimLoop on startup; stop on shutdown."""
    from app.sim_loop import SimLoop
    settings = get_settings()
    sim = SimLoop(seed=settings.seed, speed=settings.speed, hub=hub)
    sim.start()
    app.state.sim = sim
    logger.info("SimLoop started (seed=%d, speed=%.1fx)", settings.seed, settings.speed)
    yield
    sim.stop()
    logger.info("SimLoop stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    app = FastAPI(
        title="TrustHeal API",
        description=(
            "Trust-aware, explainable self-healing system for Kubernetes. "
            "Runs entirely offline against a digital twin cluster."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # REST routers
    app.include_router(health_router,    prefix="/api")
    app.include_router(sim_router,       prefix="/api/sim",      tags=["simulation"])
    app.include_router(cluster_router,   prefix="/api",          tags=["cluster"])
    app.include_router(metrics_router,   prefix="/api",          tags=["metrics"])
    app.include_router(incidents_router, prefix="/api",          tags=["incidents"])
    app.include_router(actions_router,   prefix="/api",          tags=["actions"])
    app.include_router(control_router,   prefix="/api/control",  tags=["control"])
    app.include_router(faults_router,    prefix="/api",          tags=["faults"])
    app.include_router(scenarios_router, prefix="/api",          tags=["scenarios"])
    app.include_router(audit_router,     prefix="/api",          tags=["audit"])
    app.include_router(models_router,    prefix="/api",          tags=["models"])
    app.include_router(compare_router,   prefix="/api",          tags=["compare"])

    # WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await hub.handler(websocket)

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {"message": "TrustHeal API — see /docs"}

    return app


app = create_app()
