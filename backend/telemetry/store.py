"""
telemetry/store.py — In-memory ring buffer for raw telemetry samples.

Per-service deque of ClusterSnapshot data, capped at BUFFER_SIZE samples.
Provides the sliding window used by the feature engine.
Also holds a list of ClusterEvents for the incident timeline.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from twin.cluster import ClusterSnapshot, PodPhase

# Telemetry is emitted every 15 sim-s. A 5-minute window = 20 samples.
TELEMETRY_INTERVAL_SIM: float = 15.0
WINDOW_TICKS: int = 20          # 20 × 15 s = 300 s = 5 min
BASELINE_TICKS: int = 16        # 16 × 15 s = 240 s = 4 min (baseline capture)
BUFFER_SIZE: int = 200          # keep ~50 min of history


@dataclass
class TelemetrySample:
    """One telemetry observation for one service, at one sim_time."""
    sim_time: float
    service_id: str

    # Service-level
    request_rate: float
    error_rate: float
    latency_p50: float
    latency_p95: float
    network_error_rate: float
    ready_replicas: int
    replicas: int
    hpa_desired: int | None
    revision: str
    last_revision_time: float

    # Aggregated pod-level
    cpu_util_mean: float        # mean across ready pods
    mem_ratio_mean: float       # mean across ready pods (mem_bytes / mem_limit)
    restart_count: int          # total restarts across all pods of this service
    restart_delta: int          # delta from last sample
    last_termination_reason: str | None
    last_exit_code: int | None

    # Node-level context
    ready_node_count: int
    total_node_count: int
    unknown_pod_count: int      # pods in Unknown phase for this service


class TelemetryStore:
    """
    Stores per-service telemetry samples in a ring buffer.
    Thread-safe only when accessed from a single asyncio task.
    """

    def __init__(self) -> None:
        # per-service ring buffers
        self._buffers: dict[str, deque[TelemetrySample]] = {}
        # previous restart counts for delta computation
        self._prev_restarts: dict[str, int] = {}
        # raw cluster events (for incident timeline / hard evidence)
        self._events: deque = deque(maxlen=1000)

    def record(self, snapshot: ClusterSnapshot) -> None:
        """
        Record one telemetry snapshot. Called every 15 sim-s by the SimLoop.
        """
        sim_time = snapshot.sim_time
        ready_nodes = sum(1 for n in snapshot.nodes if n.ready)
        total_nodes = len(snapshot.nodes)

        for svc in snapshot.services:
            svc_id = svc.id
            pods = snapshot.pods_for_service(svc_id)
            ready_pods = snapshot.ready_pods_for_service(svc_id)

            # Pod aggregates
            cpu_util_mean = (
                sum(p.cpu_util for p in ready_pods) / len(ready_pods)
                if ready_pods else 0.0
            )
            mem_ratio_mean = (
                sum(p.mem_ratio for p in ready_pods) / len(ready_pods)
                if ready_pods else 0.0
            )
            total_restarts = sum(p.restarts for p in pods)
            prev_restarts = self._prev_restarts.get(svc_id, total_restarts)
            restart_delta = max(0, total_restarts - prev_restarts)
            self._prev_restarts[svc_id] = total_restarts

            # Last termination info (most recently crashed pod)
            crashed = [p for p in pods if p.termination_reason is not None]
            last_term = crashed[-1].termination_reason if crashed else None
            last_exit = crashed[-1].exit_code if crashed else None

            unknown_pods = sum(
                1 for p in pods if p.phase == PodPhase.Unknown
            )

            sample = TelemetrySample(
                sim_time=sim_time,
                service_id=svc_id,
                request_rate=svc.request_rate,
                error_rate=svc.error_rate,
                latency_p50=svc.latency_p50,
                latency_p95=svc.latency_p95,
                network_error_rate=svc.network_error_rate,
                ready_replicas=svc.ready_replicas,
                replicas=svc.replicas,
                hpa_desired=svc.hpa_desired,
                revision=svc.revision,
                last_revision_time=svc.last_revision_time,
                cpu_util_mean=cpu_util_mean,
                mem_ratio_mean=mem_ratio_mean,
                restart_count=total_restarts,
                restart_delta=restart_delta,
                last_termination_reason=last_term,
                last_exit_code=last_exit,
                ready_node_count=ready_nodes,
                total_node_count=total_nodes,
                unknown_pod_count=unknown_pods,
            )

            if svc_id not in self._buffers:
                self._buffers[svc_id] = deque(maxlen=BUFFER_SIZE)
            self._buffers[svc_id].append(sample)

        # Store events
        for evt in snapshot.events:
            self._events.append(evt)

    def get_window(self, service_id: str,
                   n: int = WINDOW_TICKS) -> list[TelemetrySample]:
        """Return the last n samples for a service (oldest first)."""
        buf = self._buffers.get(service_id)
        if buf is None:
            return []
        samples = list(buf)
        return samples[-n:]

    def get_baseline_window(self, service_id: str) -> list[TelemetrySample]:
        """Return up to the first BASELINE_TICKS samples (healthy baseline)."""
        buf = self._buffers.get(service_id)
        if buf is None:
            return []
        return list(buf)[:BASELINE_TICKS]

    def has_full_window(self, service_id: str) -> bool:
        """True if at least WINDOW_TICKS samples exist for this service."""
        buf = self._buffers.get(service_id)
        return buf is not None and len(buf) >= WINDOW_TICKS

    def has_baseline(self, service_id: str) -> bool:
        """True if at least BASELINE_TICKS samples exist (baseline capturable)."""
        buf = self._buffers.get(service_id)
        return buf is not None and len(buf) >= BASELINE_TICKS

    def services(self) -> list[str]:
        return list(self._buffers.keys())

    def recent_events(self, n: int = 100) -> list:
        return list(self._events)[-n:]
