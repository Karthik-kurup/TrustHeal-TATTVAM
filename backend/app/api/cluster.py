"""app/api/cluster.py — Cluster topology and state endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/cluster")
async def get_cluster(request: Request):
    sim = request.app.state.sim
    snap = sim.cluster_state.snapshot()
    return {
        "sim_time": snap.sim_time,
        "nodes": [n.model_dump() for n in snap.nodes],
        "pods": [p.model_dump() for p in snap.pods],
        "services": [s.model_dump() for s in snap.services],
        "active_faults": [
            {
                "id": f.id,
                "kind": f.kind.value,
                "target": f.target,
                "intensity": f.spec.intensity,
                "injected_at": f.injected_at,
                "progress": f.progress(snap.sim_time),
            }
            for f in sim.faults.active()
        ],
    }
