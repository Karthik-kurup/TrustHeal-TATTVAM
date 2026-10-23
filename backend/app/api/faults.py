"""app/api/faults.py — Fault injection and management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any

from twin.faults import FaultSpec, FaultKind, OnsetShape

router = APIRouter()


class FaultInjectRequest(BaseModel):
    kind: str
    target: str
    intensity: float = 1.0
    onset_shape: str = "sigmoid"
    onset_duration: float = 30.0
    duration: float = 300.0
    metadata: dict[str, Any] = {}


@router.post("/faults/inject")
async def inject_fault(body: FaultInjectRequest, request: Request):
    sim = request.app.state.sim
    try:
        spec = FaultSpec(
            kind=FaultKind(body.kind),
            target=body.target,
            intensity=body.intensity,
            onset_shape=OnsetShape(body.onset_shape),
            onset_duration=body.onset_duration,
            duration=body.duration,
            metadata=body.metadata,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    fault_id = sim.inject_fault(spec)
    return {"ok": True, "fault_id": fault_id, "kind": body.kind, "target": body.target}


@router.get("/faults/active")
async def list_active_faults(request: Request):
    sim = request.app.state.sim
    return [
        {
            "id": f.id,
            "kind": f.kind.value,
            "target": f.target,
            "intensity": f.spec.intensity,
            "injected_at": f.injected_at,
            "progress": f.progress(sim.sim_time),
        }
        for f in sim.faults.active()
    ]


@router.delete("/faults/{fault_id}")
async def clear_fault(fault_id: str, request: Request):
    sim = request.app.state.sim
    ok = sim.clear_fault(fault_id)
    if not ok:
        raise HTTPException(404, f"Fault {fault_id!r} not found")
    return {"ok": True, "fault_id": fault_id}
