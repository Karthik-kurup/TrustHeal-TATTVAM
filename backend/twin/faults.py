"""
twin/faults.py — Fault catalogue and active fault instances.

12 faults per the master plan: 7 known (used for training) + 5 unseen
(used only for evaluation). Symptoms intentionally overlap between pairs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OnsetShape(str, Enum):
    """How the fault effect ramps in."""
    step     = "step"       # instant
    linear   = "linear"     # gradual ramp over onset_duration
    sigmoid  = "sigmoid"    # smooth S-curve ramp


class FaultKind(str, Enum):
    # Known faults (training)
    cpu_stress          = "cpu_stress"
    traffic_surge       = "traffic_surge"
    memory_exhaustion   = "memory_exhaustion"
    memory_leak         = "memory_leak"
    network_loss        = "network_loss"
    bad_deploy          = "bad_deploy"
    node_failure        = "node_failure"
    # Unseen faults (evaluation only — never train on these)
    pod_failure         = "pod_failure"
    compound            = "compound"
    dns_failure         = "dns_failure"
    slow_dependency     = "slow_dependency"
    disk_io_pressure    = "disk_io_pressure"


# Ground-truth labels for evaluation
FAULT_TRUTH_LABEL: dict[FaultKind, str] = {
    FaultKind.cpu_stress:        "CPU_OVERLOAD",
    FaultKind.traffic_surge:     "TRAFFIC_OVERLOAD",
    FaultKind.memory_exhaustion: "MEMORY_EXHAUSTION",
    FaultKind.memory_leak:       "MEMORY_LEAK",
    FaultKind.network_loss:      "NETWORK_PARTITION",
    FaultKind.bad_deploy:        "BAD_DEPLOYMENT",
    FaultKind.node_failure:      "NODE_FAILURE",
    # Unseen → UNKNOWN label
    FaultKind.pod_failure:       "UNKNOWN",
    FaultKind.compound:          "UNKNOWN",
    FaultKind.dns_failure:       "UNKNOWN",
    FaultKind.slow_dependency:   "UNKNOWN",
    FaultKind.disk_io_pressure:  "UNKNOWN",
}

KNOWN_FAULT_KINDS: set[FaultKind] = {
    FaultKind.cpu_stress, FaultKind.traffic_surge, FaultKind.memory_exhaustion,
    FaultKind.memory_leak, FaultKind.network_loss, FaultKind.bad_deploy,
    FaultKind.node_failure,
}

UNSEEN_FAULT_KINDS: set[FaultKind] = {
    FaultKind.pod_failure, FaultKind.compound, FaultKind.dns_failure,
    FaultKind.slow_dependency, FaultKind.disk_io_pressure,
}


@dataclass
class FaultSpec:
    """
    Specification for injecting a fault — matches the REST API body.
    """
    kind: FaultKind
    target: str                           # service id, node id, or pod id
    intensity: float = 1.0                # 0‥1 scale for severity
    onset_shape: OnsetShape = OnsetShape.sigmoid
    onset_duration: float = 30.0          # sim-seconds for ramp
    duration: float = 300.0               # sim-seconds active (0 = permanent)
    metadata: dict[str, Any] = field(default_factory=dict)  # e.g. {"new_revision": "v2"}


@dataclass
class ActiveFault:
    """
    A live fault instance injected into the cluster.
    Created from a FaultSpec; tracks progress and expiry.
    """
    id: str
    spec: FaultSpec
    injected_at: float           # sim_time of injection

    def progress(self, sim_time: float) -> float:
        """
        Return how much of the fault's effect is currently active [0‥1].
        0 = just injected, 1 = fully ramped in.
        """
        elapsed = sim_time - self.injected_at
        if self.spec.onset_duration <= 0:
            return 1.0
        t = min(1.0, elapsed / self.spec.onset_duration)
        if self.spec.onset_shape == OnsetShape.step:
            return 1.0
        elif self.spec.onset_shape == OnsetShape.linear:
            return t
        else:  # sigmoid
            # Standard sigmoid scaled so s(0)≈0.01 and s(1)≈0.99
            k = 10.0
            return 1.0 / (1.0 + __import__('math').exp(-k * (t - 0.5)))

    def is_expired(self, sim_time: float) -> bool:
        if self.spec.duration <= 0:
            return False  # permanent until cleared
        return sim_time >= self.injected_at + self.spec.onset_duration + self.spec.duration

    def effective_intensity(self, sim_time: float) -> float:
        """Intensity scaled by ramp progress."""
        return self.spec.intensity * self.progress(sim_time)

    @property
    def kind(self) -> FaultKind:
        return self.spec.kind

    @property
    def target(self) -> str:
        return self.spec.target

    @property
    def truth_label(self) -> str:
        return FAULT_TRUTH_LABEL[self.kind]


class FaultRegistry:
    """
    Registry of active fault instances for one simulation arm.
    Thread-safe only when accessed from a single asyncio task.
    """

    def __init__(self) -> None:
        self._faults: dict[str, ActiveFault] = {}
        self._counter: int = 0

    def inject(self, spec: FaultSpec, sim_time: float) -> str:
        """Inject a fault; return its id."""
        fault_id = f"fault-{self._counter}"
        self._counter += 1
        self._faults[fault_id] = ActiveFault(
            id=fault_id,
            spec=spec,
            injected_at=sim_time,
        )
        return fault_id

    def clear(self, fault_id: str) -> bool:
        return bool(self._faults.pop(fault_id, None))

    def clear_all(self) -> None:
        self._faults.clear()

    def tick(self, sim_time: float) -> list[str]:
        """Remove expired faults; return list of cleared ids."""
        expired = [fid for fid, f in self._faults.items() if f.is_expired(sim_time)]
        for fid in expired:
            del self._faults[fid]
        return expired

    def active(self) -> list[ActiveFault]:
        return list(self._faults.values())

    def for_target(self, target: str) -> list[ActiveFault]:
        return [f for f in self._faults.values() if f.target == target]

    def of_kind(self, kind: FaultKind) -> list[ActiveFault]:
        return [f for f in self._faults.values() if f.kind == kind]

    def __len__(self) -> int:
        return len(self._faults)
