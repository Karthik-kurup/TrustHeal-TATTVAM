"""
twin/cluster.py — Core Pydantic models for the digital twin cluster state.

All mutable state lives in ClusterState, which is the single source of truth
passed to dynamics.step() each physics tick.
"""

from __future__ import annotations

import copy
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class PodPhase(str, Enum):
    Running = "Running"
    Pending = "Pending"
    Unknown = "Unknown"
    Succeeded = "Succeeded"
    Failed = "Failed"
    CrashLoopBackOff = "CrashLoopBackOff"
    OOMKilled = "OOMKilled"
    Terminating = "Terminating"


class NodeCondition(str, Enum):
    Ready = "Ready"
    NotReady = "NotReady"
    Unknown = "Unknown"


class EventReason(str, Enum):
    OOMKilled = "OOMKilled"
    BackOff = "BackOff"
    NodeNotReady = "NodeNotReady"
    Pulled = "Pulled"
    Started = "Started"
    Killing = "Killing"
    Rescheduled = "Rescheduled"
    ScaledUp = "ScaledUp"
    ScaledDown = "ScaledDown"
    Rollback = "Rollback"
    FaultInjected = "FaultInjected"
    FaultCleared = "FaultCleared"
    ActionExecuted = "ActionExecuted"


# ── Leaf models ───────────────────────────────────────────────────────────────

class Pod(BaseModel):
    """Single pod state, updated every physics tick."""

    id: str                             # e.g. "frontend-0"
    service: str                        # parent service name
    node: str                           # node id currently scheduled on
    phase: PodPhase = PodPhase.Running
    ready: bool = True

    # Resource utilisation (0‥1 fractions of per-pod limits)
    cpu_util: float = 0.0               # 0 = idle, 1 = at limit
    mem_bytes: int = 0                  # current memory usage in bytes
    mem_limit_bytes: int = 256 * 1024 * 1024  # 256 MiB default

    # Traffic metrics
    request_rate: float = 0.0           # requests/s served by this pod
    error_rate: float = 0.0             # fraction of requests that error
    latency_p50: float = 0.005          # seconds
    latency_p95: float = 0.010          # seconds
    network_error_rate: float = 0.0     # fraction of packets lost/errored

    # Lifecycle
    restarts: int = 0
    termination_reason: str | None = None
    exit_code: int | None = None
    revision: str = "v1"                # Deployment revision label
    backoff_until: float = 0.0          # sim_time before which pod stays in CrashLoop

    @property
    def mem_ratio(self) -> float:
        """Memory usage as fraction of limit (0‥∞, >1 means OOM territory)."""
        if self.mem_limit_bytes == 0:
            return 0.0
        return self.mem_bytes / self.mem_limit_bytes


class Node(BaseModel):
    """Worker node state."""

    id: str                             # e.g. "node-1"
    condition: NodeCondition = NodeCondition.Ready

    # Capacity (cores / bytes)
    cpu_capacity: float = 4.0           # cores
    mem_capacity: int = 4 * 1024 * 1024 * 1024  # 4 GiB

    # Tracked usage (sum of pods on this node)
    cpu_used: float = 0.0
    mem_used: int = 0

    # Failure state tracking
    failed_at: float | None = None      # sim_time when node went down
    eviction_grace_seconds: float = 30.0

    @property
    def ready(self) -> bool:
        return self.condition == NodeCondition.Ready


class Service(BaseModel):
    """Aggregated per-service metrics, derived from pods each tick."""

    id: str
    # Desired and actual replica counts
    replicas: int = 2
    ready_replicas: int = 2

    # Aggregated traffic metrics (sum/avg across ready pods)
    request_rate: float = 0.0
    error_rate: float = 0.0
    latency_p50: float = 0.005
    latency_p95: float = 0.010
    network_error_rate: float = 0.0

    # HPA (optional)
    hpa_enabled: bool = False
    hpa_min_replicas: int = 1
    hpa_max_replicas: int = 6           # replica ceiling per spec
    hpa_target_cpu: float = 0.7
    hpa_desired: int | None = None

    # Deployment
    revision: str = "v1"
    last_revision_time: float = 0.0     # sim_time of last rollout

    # Calls: list of service ids this service calls
    calls: list[str] = Field(default_factory=list)

    # Per-pod CPU and memory limits (shared across pods of this service)
    cpu_limit_per_pod: float = 0.5      # cores
    mem_limit_per_pod: int = 256 * 1024 * 1024  # 256 MiB


class ClusterEvent(BaseModel):
    """A discrete event emitted by the twin (Kubernetes-style)."""

    sim_time: float
    reason: EventReason
    source: str                         # pod/node/service id
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Snapshot ──────────────────────────────────────────────────────────────────

class ClusterSnapshot(BaseModel):
    """
    Point-in-time read-only view of the cluster.
    Emitted by SimAdapter.get_snapshot() every telemetry tick (15 sim-s).
    """

    sim_time: float
    nodes: list[Node]
    pods: list[Pod]
    services: list[Service]
    events: list[ClusterEvent] = Field(default_factory=list)

    def pods_for_service(self, service_id: str) -> list[Pod]:
        return [p for p in self.pods if p.service == service_id]

    def ready_pods_for_service(self, service_id: str) -> list[Pod]:
        return [
            p for p in self.pods
            if p.service == service_id and p.ready and p.phase == PodPhase.Running
        ]

    def node_by_id(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)


# ── Mutable cluster state ─────────────────────────────────────────────────────

class ClusterState:
    """
    Mutable cluster state. Not a Pydantic model — mutated in-place by dynamics.step().
    Call .snapshot() to get an immutable ClusterSnapshot for telemetry.
    Call .deep_copy() for snapshot/restore.
    """

    def __init__(self) -> None:
        self.sim_time: float = 0.0
        self.nodes: dict[str, Node] = {}
        self.pods: dict[str, Pod] = {}
        self.services: dict[str, Service] = {}
        self.pending_events: list[ClusterEvent] = []

    def add_node(self, node: Node) -> None:
        self.nodes[node.id] = node

    def add_pod(self, pod: Pod) -> None:
        self.pods[pod.id] = pod

    def add_service(self, service: Service) -> None:
        self.services[service.id] = service

    def emit_event(self, reason: EventReason, source: str, message: str,
                   metadata: dict[str, Any] | None = None) -> None:
        self.pending_events.append(ClusterEvent(
            sim_time=self.sim_time,
            reason=reason,
            source=source,
            message=message,
            metadata=metadata or {},
        ))

    def drain_events(self) -> list[ClusterEvent]:
        """Return and clear pending events."""
        events = list(self.pending_events)
        self.pending_events.clear()
        return events

    def snapshot(self) -> ClusterSnapshot:
        """Return an immutable snapshot of current state."""
        return ClusterSnapshot(
            sim_time=self.sim_time,
            nodes=list(self.nodes.values()),
            pods=list(self.pods.values()),
            services=list(self.services.values()),
            events=[],  # events are drained separately
        )

    def deep_copy(self) -> "ClusterState":
        """Return a deep copy for snapshot/restore."""
        new = ClusterState()
        new.sim_time = self.sim_time
        new.nodes = {k: v.model_copy(deep=True) for k, v in self.nodes.items()}
        new.pods = {k: v.model_copy(deep=True) for k, v in self.pods.items()}
        new.services = {k: v.model_copy(deep=True) for k, v in self.services.items()}
        new.pending_events = copy.deepcopy(self.pending_events)
        return new

    def pods_for_service(self, service_id: str) -> list[Pod]:
        return [p for p in self.pods.values() if p.service == service_id]

    def ready_pods_for_service(self, service_id: str) -> list[Pod]:
        return [
            p for p in self.pods.values()
            if p.service == service_id and p.ready and p.phase == PodPhase.Running
        ]

    def nodes_ready(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.ready]


# ── Shop topology factory ─────────────────────────────────────────────────────

# Service start replica counts (total 14 pods)
SHOP_SERVICES: dict[str, dict] = {
    "frontend":  {"replicas": 3, "calls": ["catalog", "cart", "checkout"],
                  "cpu_limit": 0.5, "mem_limit_mb": 256},
    "catalog":   {"replicas": 3, "calls": ["cache"],
                  "cpu_limit": 0.4, "mem_limit_mb": 256},
    "cart":      {"replicas": 2, "calls": [],
                  "cpu_limit": 0.3, "mem_limit_mb": 128},
    "checkout":  {"replicas": 3, "calls": ["payment", "cart"],
                  "cpu_limit": 0.5, "mem_limit_mb": 256},
    "payment":   {"replicas": 2, "calls": [],
                  "cpu_limit": 0.4, "mem_limit_mb": 128},
    "cache":     {"replicas": 1, "calls": [],
                  "cpu_limit": 0.2, "mem_limit_mb": 512},
}

# Node capacity: 4 nodes, sized so any one can be lost and all 14 pods still fit.
# Worst case: 3 nodes must host 14 pods.  Each pod uses max ~0.5 cores / 256 MiB.
# 14 × 0.5 = 7 cores → 3 nodes × 3 cores = 9 cores  (headroom ✓)
# 14 × 256 MiB = 3.5 GiB → 3 nodes × 2 GiB = 6 GiB  (headroom ✓)
NODE_CONFIGS: list[dict] = [
    {"id": "node-1", "cpu_capacity": 3.0, "mem_capacity_gib": 2},
    {"id": "node-2", "cpu_capacity": 3.0, "mem_capacity_gib": 2},
    {"id": "node-3", "cpu_capacity": 3.0, "mem_capacity_gib": 2},
    {"id": "node-4", "cpu_capacity": 3.0, "mem_capacity_gib": 2},
]


def build_shop_cluster() -> ClusterState:
    """
    Build the initial Shop namespace cluster state.
    14 pods across 4 nodes, 6 services per the master plan topology.
    """
    state = ClusterState()

    # Create nodes
    for cfg in NODE_CONFIGS:
        state.add_node(Node(
            id=cfg["id"],
            cpu_capacity=cfg["cpu_capacity"],
            mem_capacity=cfg["mem_capacity_gib"] * 1024 * 1024 * 1024,
        ))
    node_ids = list(state.nodes.keys())

    # Create services and pods
    pod_index = 0
    for svc_id, cfg in SHOP_SERVICES.items():
        mem_limit = cfg["mem_limit_mb"] * 1024 * 1024
        state.add_service(Service(
            id=svc_id,
            replicas=cfg["replicas"],
            ready_replicas=cfg["replicas"],
            calls=cfg["calls"],
            cpu_limit_per_pod=cfg["cpu_limit"],
            mem_limit_per_pod=mem_limit,
            hpa_enabled=(svc_id in ("frontend", "catalog", "checkout")),
            hpa_min_replicas=cfg["replicas"],
            hpa_max_replicas=6,
            hpa_target_cpu=0.70,
        ))
        for i in range(cfg["replicas"]):
            node_id = node_ids[pod_index % len(node_ids)]
            pod_index += 1
            baseline_mem = int(mem_limit * 0.20)   # 20% baseline memory usage
            state.add_pod(Pod(
                id=f"{svc_id}-{i}",
                service=svc_id,
                node=node_id,
                mem_bytes=baseline_mem,
                mem_limit_bytes=mem_limit,
                cpu_util=0.0,
            ))

    return state
