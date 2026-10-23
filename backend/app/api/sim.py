"""app/api/sim.py — Simulation control endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.sim_loop import SimStatus

router = APIRouter()


class SimStateResponse(BaseModel):
    status: str
    sim_time: float
    speed: float
    seed: int
    active_faults: int
    open_incidents: int


class SimControlRequest(BaseModel):
    cmd: str          # play | pause | step | speed | reset | seed
    value: float | None = None


class SnapshotResponse(BaseModel):
    snapshot_id: str


class RestoreRequest(BaseModel):
    snapshot_id: str


def _sim(req: Request):
    sim = getattr(req.app.state, "sim", None)
    if sim is None:
        raise HTTPException(503, "SimLoop not initialised")
    return sim


@router.get("/state", response_model=SimStateResponse)
async def get_sim_state(request: Request):
    sim = _sim(request)
    return SimStateResponse(
        status=sim.status.value,
        sim_time=sim.sim_time,
        speed=sim.speed,
        seed=sim.seed,
        active_faults=len(sim.faults),
        open_incidents=len(sim.open_incidents),
    )


@router.post("/control")
async def control_sim(body: SimControlRequest, request: Request):
    sim = _sim(request)
    cmd = body.cmd
    if cmd == "play":
        if sim.status == SimStatus.paused:
            sim.resume()
        elif sim.status == SimStatus.stopped:
            sim.start()
    elif cmd == "pause":
        sim.pause()
    elif cmd == "step":
        sim.step_physics()
    elif cmd == "speed":
        if body.value is None:
            raise HTTPException(422, "value required for speed command")
        sim.speed = body.value
    elif cmd == "reset":
        seed = int(body.value) if body.value is not None else None
        sim.reset(seed=seed)
    elif cmd == "seed":
        if body.value is None:
            raise HTTPException(422, "value required for seed command")
        sim.reset(seed=int(body.value))
    else:
        raise HTTPException(422, f"Unknown command: {cmd}")
    return {"ok": True, "status": sim.status.value, "sim_time": sim.sim_time}


@router.post("/snapshot", response_model=SnapshotResponse)
async def take_snapshot(request: Request):
    sim = _sim(request)
    snap_id = sim.save_snapshot()
    return SnapshotResponse(snapshot_id=snap_id)


@router.post("/restore")
async def restore_snapshot(body: RestoreRequest, request: Request):
    sim = _sim(request)
    ok = sim.restore_snapshot(body.snapshot_id)
    if not ok:
        raise HTTPException(404, f"Snapshot {body.snapshot_id!r} not found")
    return {"ok": True, "sim_time": sim.sim_time}


@router.get("/snapshots")
async def list_snapshots(request: Request):
    sim = _sim(request)
    return sim.snapshots.list()
