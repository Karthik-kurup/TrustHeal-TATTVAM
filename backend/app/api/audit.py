"""app/api/audit.py - Audit log endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

router = APIRouter()


@router.get("/audit")
async def get_audit(
    request: Request,
    service: str | None = Query(None),
    event_type: str | None = Query(None),
    limit: int = Query(100),
    offset: int = Query(0),
):
    sim = request.app.state.sim
    entries = sim.audit.list(limit=limit, offset=offset,
                              service=service, event_type=event_type)
    return [e.to_dict() for e in entries]
