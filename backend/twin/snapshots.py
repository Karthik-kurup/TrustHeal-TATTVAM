"""
twin/snapshots.py — Snapshot and restore for the cluster state.

Supports rewind (restore to before a fault) and what-if branching.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from twin.cluster import ClusterState
from twin.faults import FaultRegistry
from twin.workload import LoadGenerator, RngStreams


@dataclass
class Snapshot:
    """A frozen point-in-time copy of the full simulation state."""
    id: str
    sim_time: float
    label: str                          # human-readable description
    cluster_state: ClusterState         # deep copy
    fault_registry_data: list[dict]     # serialised fault specs
    rng_state_workload: object          # RNG state for workload stream
    rng_state_fault: object             # RNG state for fault_schedule stream
    rng_state_noise: object             # RNG state for dynamics_noise stream


class SnapshotStore:
    """
    In-memory snapshot store. Max 20 snapshots retained (oldest pruned).
    """
    MAX_SNAPSHOTS = 20

    def __init__(self) -> None:
        self._snapshots: dict[str, Snapshot] = {}
        self._order: list[str] = []

    def save(self, state: ClusterState, faults: FaultRegistry,
             load_gen: LoadGenerator, label: str = "") -> str:
        """Take a snapshot and return its id."""
        snap_id = str(uuid.uuid4())[:8]
        snap = Snapshot(
            id=snap_id,
            sim_time=state.sim_time,
            label=label or f"t={state.sim_time:.0f}s",
            cluster_state=state.deep_copy(),
            fault_registry_data=[
                {
                    "id": f.id,
                    "kind": f.spec.kind.value,
                    "target": f.spec.target,
                    "intensity": f.spec.intensity,
                    "onset_shape": f.spec.onset_shape.value,
                    "onset_duration": f.spec.onset_duration,
                    "duration": f.spec.duration,
                    "injected_at": f.injected_at,
                    "metadata": f.spec.metadata,
                }
                for f in faults.active()
            ],
            rng_state_workload=load_gen.rng.workload.getstate(),
            rng_state_fault=load_gen.rng.fault_schedule.getstate(),
            rng_state_noise=load_gen.rng.dynamics_noise.getstate(),
        )
        self._snapshots[snap_id] = snap
        self._order.append(snap_id)

        # Prune oldest if over limit
        while len(self._order) > self.MAX_SNAPSHOTS:
            oldest = self._order.pop(0)
            self._snapshots.pop(oldest, None)

        return snap_id

    def restore(self, snap_id: str, state: ClusterState, faults: FaultRegistry,
                load_gen: LoadGenerator) -> bool:
        """
        Restore simulation to a snapshot.
        Returns True on success, False if snapshot not found.
        """
        snap = self._snapshots.get(snap_id)
        if snap is None:
            return False

        # Restore cluster state
        restored = snap.cluster_state.deep_copy()
        state.__dict__.update(restored.__dict__)

        # Restore faults
        from twin.faults import ActiveFault, FaultSpec, FaultKind, OnsetShape
        faults.clear_all()
        for fd in snap.fault_registry_data:
            spec = FaultSpec(
                kind=FaultKind(fd["kind"]),
                target=fd["target"],
                intensity=fd["intensity"],
                onset_shape=OnsetShape(fd["onset_shape"]),
                onset_duration=fd["onset_duration"],
                duration=fd["duration"],
                metadata=fd.get("metadata", {}),
            )
            af = ActiveFault(id=fd["id"], spec=spec, injected_at=fd["injected_at"])
            faults._faults[af.id] = af

        # Restore RNG streams
        load_gen.rng.workload.setstate(snap.rng_state_workload)
        load_gen.rng.fault_schedule.setstate(snap.rng_state_fault)
        load_gen.rng.dynamics_noise.setstate(snap.rng_state_noise)

        return True

    def list(self) -> list[dict]:
        return [
            {"id": s.id, "sim_time": s.sim_time, "label": s.label}
            for s in (self._snapshots[sid] for sid in self._order)
        ]

    def get(self, snap_id: str) -> Snapshot | None:
        return self._snapshots.get(snap_id)
