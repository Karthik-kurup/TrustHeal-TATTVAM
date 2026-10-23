"""
twin/dynamics.py — Per-tick physics for the digital twin.

Runs at dt=1 simulated second. Handles:
  - CPU utilisation model (queueing-based)
  - Memory model (baseline + per-RPS + leak slope + OOMKilled)
  - Latency model (queueing curve)
  - Error model (from utilisation, OOM, bad_deploy, network_loss)
  - Dependency propagation (cascade from callees to callers)
  - Fault application for all 12 fault types
  - Kubernetes-native reactions (restart, CrashLoopBackOff, readiness gating)
  - Node/pod aggregation update
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from twin.cluster import (
    ClusterState, EventReason, NodeCondition, PodPhase,
)
from twin.faults import ActiveFault, FaultKind, FaultRegistry
from twin.workload import (
    LoadGenerator,
    SERVICE_BASELINE_MEM_FRACTION,
    SERVICE_MEM_PER_RPS,
)

if TYPE_CHECKING:
    pass


# ── Physics constants ─────────────────────────────────────────────────────────

# Latency model: M/D/1 queueing approximation
# latency = base_latency / (1 - utilisation)  but capped at saturation
BASE_LATENCY_S: dict[str, float] = {
    "frontend":  0.005,
    "catalog":   0.008,
    "cart":      0.004,
    "checkout":  0.010,
    "payment":   0.012,
    "cache":     0.002,
}
SATURATION_LATENCY_MULT: float = 20.0   # max latency multiplier at util=1

# Error rate thresholds
ERROR_AT_OVERLOAD: float = 0.25         # error rate when util >= 1.0
ERROR_AT_OOM: float = 0.50              # error rate when mem > limit
ERROR_NETWORK_LOSS_MULT: float = 0.80   # most network errors become errors

# Memory model
MEM_SMOOTH_ALPHA: float = 0.1           # EMA factor for memory (slow change)
LEAK_BYTES_PER_SECOND: float = 8192.0   # base leak rate (intensity scales this)

# CrashLoopBackOff exponential backoff
CRASHLOOP_BACKOFF_BASE: float = 10.0    # seconds before first restart
CRASHLOOP_BACKOFF_MAX: float = 300.0    # cap at 5 minutes
CRASHLOOP_RESTART_RECOVERY_S: float = 5.0  # sim-seconds to go Running after restart

# Node failure
NODE_NOTREADY_GRACE_S: float = 30.0     # seconds after failure before pods go Unknown
NODE_EVICTION_TIMEOUT_S: float = 60.0   # seconds before Unknown pods are evicted/rescheduled


def _latency_from_util(base: float, util: float) -> float:
    """Queueing latency: rises sharply near util=1."""
    util_capped = min(util, 0.999)
    return base / max(0.001, 1.0 - util_capped)


def _p95_from_p50(p50: float) -> float:
    """Approximate p95 as ~2× p50 (realistic for a moderately tail-heavy workload)."""
    return p50 * 2.0


# ── Main step function ────────────────────────────────────────────────────────

def step(
    state: ClusterState,
    load_gen: LoadGenerator,
    faults: FaultRegistry,
    dt: float = 1.0,
) -> None:
    """
    Advance cluster state by dt simulated seconds (default 1s).
    Mutates state in-place. Emits events via state.emit_event().
    """
    sim_time = state.sim_time

    # ── 1. Expire faults ─────────────────────────────────────────────────────
    expired_ids = faults.tick(sim_time)
    for fid in expired_ids:
        state.emit_event(EventReason.FaultCleared, "fault-registry",
                         f"Fault {fid} expired")

    # ── 2. Advance load generator ─────────────────────────────────────────────
    load_gen.tick(sim_time, list(state.services.keys()))

    # ── 3. Node failure handling ──────────────────────────────────────────────
    _apply_node_failures(state, faults, sim_time)

    # ── 4. Per-service physics ────────────────────────────────────────────────
    # Collect callee metrics first (for dependency propagation)
    callee_metrics: dict[str, dict] = {}

    # Two-pass: first compute independent services (no callees in trouble),
    # then propagate cascade.
    for svc_id, svc in state.services.items():
        _compute_service_metrics(state, svc_id, load_gen, faults, sim_time, dt,
                                 callee_metrics)

    # ── 5. Dependency cascade propagation ────────────────────────────────────
    _propagate_cascades(state, callee_metrics)

    # ── 6. OOM / crash / restart handling ────────────────────────────────────
    for pod in list(state.pods.values()):
        _handle_pod_lifecycle(state, pod, faults, sim_time)

    # ── 7. Update service aggregates ─────────────────────────────────────────
    for svc_id, svc in state.services.items():
        _aggregate_service(state, svc_id)

    # ── 8. Update node resource usage ────────────────────────────────────────
    for node in state.nodes.values():
        pods_on_node = [p for p in state.pods.values() if p.node == node.id]
        node.cpu_used = sum(
            p.cpu_util * state.services[p.service].cpu_limit_per_pod
            for p in pods_on_node
            if p.service in state.services
        )
        node.mem_used = sum(p.mem_bytes for p in pods_on_node)

    # ── 9. Advance sim clock ──────────────────────────────────────────────────
    state.sim_time += dt


def _apply_node_failures(state: ClusterState, faults: FaultRegistry,
                         sim_time: float) -> None:
    """Handle node_failure faults and their effects on pods."""
    for fault in faults.of_kind(FaultKind.node_failure):
        node_id = fault.target
        node = state.nodes.get(node_id)
        if node is None:
            continue

        if node.condition == NodeCondition.Ready:
            # First time we see this fault — fail the node
            node.condition = NodeCondition.NotReady
            node.failed_at = sim_time
            state.emit_event(EventReason.NodeNotReady, node_id,
                             f"Node {node_id} went NotReady (node_failure fault)")

        elapsed_since_failure = sim_time - (node.failed_at or sim_time)

        # Pods go Unknown after grace period
        if elapsed_since_failure >= NODE_NOTREADY_GRACE_S:
            for pod in state.pods.values():
                if pod.node == node_id and pod.phase not in (
                    PodPhase.Unknown, PodPhase.Pending
                ):
                    pod.phase = PodPhase.Unknown
                    pod.ready = False

    # Recover nodes that no longer have a failure fault
    for node in state.nodes.values():
        if node.condition == NodeCondition.NotReady:
            has_fault = any(
                f.target == node.id
                for f in faults.of_kind(FaultKind.node_failure)
            )
            if not has_fault:
                node.condition = NodeCondition.Ready
                node.failed_at = None


def _get_fault_effects(faults: FaultRegistry, target: str,
                       sim_time: float) -> dict:
    """
    Collect all fault modifiers for a given target (service or pod id).
    Returns a dict of named effects with float magnitudes [0‥1+].
    """
    effects: dict = {
        "cpu_extra": 0.0,          # extra CPU load fraction
        "traffic_multiplier": 1.0, # multiply request rate
        "mem_exhaustion": 0.0,     # push memory toward limit (fraction of gap)
        "mem_leak_rate": 0.0,      # bytes/second leak
        "network_loss": 0.0,       # packet loss fraction
        "error_extra": 0.0,        # extra error rate (e.g. bad_deploy)
        "latency_extra_ms": 0.0,   # extra latency in ms (slow_dependency, dns)
        "io_pressure": 0.0,        # disk I/O pressure (increases latency)
    }
    for fault in faults.for_target(target):
        intensity = fault.effective_intensity(sim_time)
        k = fault.kind

        if k == FaultKind.cpu_stress:
            effects["cpu_extra"] += intensity * 0.6

        elif k == FaultKind.traffic_surge:
            effects["traffic_multiplier"] *= (1.0 + intensity * 3.0)  # up to 4×

        elif k == FaultKind.memory_exhaustion:
            effects["mem_exhaustion"] += intensity * 0.8  # push to 80%+ of limit

        elif k == FaultKind.memory_leak:
            effects["mem_leak_rate"] += intensity * LEAK_BYTES_PER_SECOND * 50.0

        elif k == FaultKind.network_loss:
            effects["network_loss"] += intensity * 0.6   # up to 60% packet loss

        elif k == FaultKind.bad_deploy:
            effects["error_extra"] += intensity * 0.35   # up to 35% errors from bad version

        elif k == FaultKind.pod_failure:
            # Unseen: random crash (handled in lifecycle, not here)
            pass

        elif k == FaultKind.compound:
            # Unseen: cpu_stress + network_loss
            effects["cpu_extra"] += intensity * 0.5
            effects["network_loss"] += intensity * 0.4

        elif k == FaultKind.dns_failure:
            # Unseen: name resolution failing → timeout latency + errors
            effects["latency_extra_ms"] += intensity * 500.0  # 500ms DNS timeout
            effects["error_extra"] += intensity * 0.20

        elif k == FaultKind.slow_dependency:
            # Unseen: downstream slow (similar to network_loss in symptoms)
            effects["latency_extra_ms"] += intensity * 200.0
            effects["error_extra"] += intensity * 0.10

        elif k == FaultKind.disk_io_pressure:
            # Unseen: I/O latency rising
            effects["io_pressure"] += intensity * 0.7
            effects["latency_extra_ms"] += intensity * 100.0

    return effects


def _compute_service_metrics(
    state: ClusterState, svc_id: str,
    load_gen: LoadGenerator, faults: FaultRegistry,
    sim_time: float, dt: float,
    callee_metrics: dict,
) -> None:
    """Compute per-pod metrics for one service."""
    svc = state.services[svc_id]
    ready_pods = state.ready_pods_for_service(svc_id)
    if not ready_pods:
        # No ready pods — service is down, max errors
        svc.request_rate = 0.0
        svc.error_rate = 1.0
        svc.latency_p50 = BASE_LATENCY_S.get(svc_id, 0.01) * SATURATION_LATENCY_MULT
        svc.latency_p95 = svc.latency_p50 * 2.0
        callee_metrics[svc_id] = {
            "error_rate": 1.0,
            "latency_p50": svc.latency_p50,
        }
        return

    n_ready = len(ready_pods)
    fx = _get_fault_effects(faults, svc_id, sim_time)
    total_rps = load_gen.request_rate(svc_id, sim_time,
                                      fault_multiplier=fx["traffic_multiplier"])
    rps_per_pod = total_rps / n_ready
    base_lat = BASE_LATENCY_S.get(svc_id, 0.01)
    mem_limit = svc.mem_limit_per_pod
    baseline_mem_frac = SERVICE_BASELINE_MEM_FRACTION.get(svc_id, 0.25)

    for pod in ready_pods:
        noise_rng = load_gen.rng.dynamics_noise
        # ── CPU ──────────────────────────────────────────────────────────────
        # CPU util = (rps_per_pod / service_capacity) + extra fault load
        # service capacity is defined so that at baseline peak rps, util ~ 0.5
        peak_rps = load_gen.baseline_rps(svc_id, sim_time)
        capacity_rps = (peak_rps / n_ready) * 2.0  # headroom: 2× peak is 100% util
        capacity_rps = max(capacity_rps, 1.0)
        cpu_from_load = rps_per_pod / capacity_rps
        cpu_util = min(2.0, cpu_from_load + fx["cpu_extra"] +
                       noise_rng.gauss(0, 0.02))
        pod.cpu_util = max(0.0, cpu_util)

        # ── Latency ──────────────────────────────────────────────────────────
        util_for_latency = min(pod.cpu_util, 0.999)
        p50 = _latency_from_util(base_lat, util_for_latency)
        p50 = min(p50, base_lat * SATURATION_LATENCY_MULT)
        p50 += fx["latency_extra_ms"] / 1000.0
        p50 += fx["io_pressure"] * 0.5  # I/O adds latency
        p50 = max(base_lat, p50)
        pod.latency_p50 = p50
        pod.latency_p95 = _p95_from_p50(p50)

        # ── Errors ───────────────────────────────────────────────────────────
        overload_errors = max(0.0, pod.cpu_util - 1.0) * ERROR_AT_OVERLOAD
        oom_errors = ERROR_AT_OOM if pod.mem_ratio > 1.0 else 0.0
        net_errors = fx["network_loss"] * ERROR_NETWORK_LOSS_MULT
        pod.error_rate = min(1.0, overload_errors + oom_errors +
                             net_errors + fx["error_extra"] +
                             noise_rng.gauss(0, 0.01))
        pod.error_rate = max(0.0, pod.error_rate)

        # ── Network errors ────────────────────────────────────────────────────
        pod.network_error_rate = max(0.0, fx["network_loss"] +
                                     noise_rng.gauss(0, 0.01))

        # ── Memory ───────────────────────────────────────────────────────────
        # If pod is already over limit (OOM imminent), skip EMA recompute so the
        # lifecycle handler can observe the over-limit value and kill the pod.
        if pod.mem_bytes <= pod.mem_limit_bytes:
            baseline_bytes = int(mem_limit * baseline_mem_frac)
            rps_contrib = int(rps_per_pod * SERVICE_MEM_PER_RPS.get(svc_id, 800))
            exhaustion_push = int(fx["mem_exhaustion"] *
                                  (mem_limit - pod.mem_bytes) * 0.3)
            target_mem = baseline_bytes + rps_contrib + exhaustion_push
            # EMA smoothing; leak added after so it accumulates unboundedly
            pod.mem_bytes = int(
                (1 - MEM_SMOOTH_ALPHA) * pod.mem_bytes +
                MEM_SMOOTH_ALPHA * target_mem
            ) + int(fx["mem_leak_rate"] * dt)
            pod.mem_bytes = max(0, pod.mem_bytes)
        # else: keep current over-limit value so lifecycle pass triggers OOM kill

        # ── Request rate per pod ──────────────────────────────────────────────
        pod.request_rate = rps_per_pod

    # Store for cascade propagation
    avg_errors = sum(p.error_rate for p in ready_pods) / n_ready
    avg_lat = sum(p.latency_p50 for p in ready_pods) / n_ready
    callee_metrics[svc_id] = {
        "error_rate": avg_errors,
        "latency_p50": avg_lat,
    }


def _propagate_cascades(state: ClusterState, callee_metrics: dict) -> None:
    """
    Propagate degraded callees' effects upstream to callers.
    A slow or failing callee adds latency and errors to its callers.
    """
    for svc_id, svc in state.services.items():
        if not svc.calls:
            continue
        ready_pods = state.ready_pods_for_service(svc_id)
        if not ready_pods:
            continue

        cascade_error = 0.0
        cascade_lat_ms = 0.0
        for callee_id in svc.calls:
            callee = callee_metrics.get(callee_id, {})
            callee_err = callee.get("error_rate", 0.0)
            callee_lat = callee.get("latency_p50", 0.0)
            base_lat = BASE_LATENCY_S.get(callee_id, 0.01)
            # Excess latency beyond baseline propagates
            excess_lat = max(0.0, callee_lat - base_lat)
            cascade_lat_ms += excess_lat * 1000.0 * 0.4  # 40% of callee excess
            # Some callee errors become caller errors
            cascade_error += callee_err * 0.30  # 30% bleed-through

        for pod in ready_pods:
            pod.latency_p50 = min(
                pod.latency_p50 + cascade_lat_ms / 1000.0,
                BASE_LATENCY_S.get(svc_id, 0.01) * SATURATION_LATENCY_MULT,
            )
            pod.latency_p95 = _p95_from_p50(pod.latency_p50)
            pod.error_rate = min(1.0, pod.error_rate + cascade_error)


def _handle_pod_lifecycle(
    state: ClusterState, pod, faults: FaultRegistry, sim_time: float
) -> None:
    """
    Kubernetes-native pod lifecycle:
    - OOMKilled when mem > limit
    - CrashLoopBackOff with exponential backoff
    - Recovery after backoff expires
    - pod_failure fault: random crash
    """
    from twin.cluster import Pod  # local import to avoid circular at module level

    svc = state.services.get(pod.service)
    if svc is None:
        return

    # ── pod_failure (unseen) — random crash ──────────────────────────────────
    for fault in faults.for_target(pod.service):
        if fault.kind == FaultKind.pod_failure and fault.target == pod.service:
            intensity = fault.effective_intensity(sim_time)
            # Small probability per tick to crash
            import random as _r
            if _r.random() < intensity * 0.01 and pod.phase == PodPhase.Running:
                _crash_pod(state, pod, reason="Error", exit_code=1, sim_time=sim_time)
                return

    # ── OOM kill ─────────────────────────────────────────────────────────────
    if pod.phase == PodPhase.Running and pod.mem_bytes > pod.mem_limit_bytes:
        _crash_pod(state, pod, reason="OOMKilled", exit_code=137, sim_time=sim_time)
        return

    # ── CrashLoopBackOff recovery ─────────────────────────────────────────────
    if pod.phase == PodPhase.CrashLoopBackOff:
        if sim_time >= pod.backoff_until:
            pod.phase = PodPhase.Running
            pod.ready = True
            # Memory resets on restart
            pod.mem_bytes = int(pod.mem_limit_bytes *
                                SERVICE_BASELINE_MEM_FRACTION.get(pod.service, 0.25))
            state.emit_event(EventReason.Started, pod.id,
                             f"Pod {pod.id} restarted (backoff expired)")

    # ── Unknown pods on failed nodes ──────────────────────────────────────────
    if pod.phase == PodPhase.Unknown:
        node = state.nodes.get(pod.node)
        # If node recovered, pods go Pending → Running
        if node and node.ready:
            pod.phase = PodPhase.Pending
            pod.ready = False
        elif node and not node.ready:
            # Evict after timeout (handled by scheduler)
            pass


def _crash_pod(state: ClusterState, pod, reason: str, exit_code: int,
               sim_time: float) -> None:
    """Mark a pod as crashed and schedule backoff."""
    pod.restarts += 1
    pod.termination_reason = reason
    pod.exit_code = exit_code
    pod.ready = False

    if reason == "OOMKilled":
        pod.phase = PodPhase.OOMKilled
        state.emit_event(EventReason.OOMKilled, pod.id,
                         f"Pod {pod.id} OOMKilled (mem={pod.mem_bytes // (1024*1024)}MiB"
                         f" limit={pod.mem_limit_bytes // (1024*1024)}MiB)",
                         {"restarts": pod.restarts})
    else:
        pod.phase = PodPhase.CrashLoopBackOff
        state.emit_event(EventReason.BackOff, pod.id,
                         f"Pod {pod.id} in CrashLoopBackOff (exit={exit_code})",
                         {"restarts": pod.restarts})

    # Exponential backoff: 10s * 2^(restarts-1), capped at 300s
    backoff = min(
        CRASHLOOP_BACKOFF_MAX,
        CRASHLOOP_BACKOFF_BASE * (2 ** max(0, pod.restarts - 1)),
    )
    pod.backoff_until = sim_time + backoff

    # After OOMKill the pod quickly transitions to CrashLoopBackOff
    if reason == "OOMKilled":
        pod.phase = PodPhase.CrashLoopBackOff
        # Memory is not reset until restart completes


def _aggregate_service(state: ClusterState, svc_id: str) -> None:
    """Update service-level aggregated metrics from pods."""
    svc = state.services[svc_id]
    all_pods = state.pods_for_service(svc_id)
    ready_pods = [p for p in all_pods if p.ready and p.phase == PodPhase.Running]

    svc.ready_replicas = len(ready_pods)

    if not ready_pods:
        svc.error_rate = 1.0
        svc.request_rate = 0.0
        return

    svc.request_rate = sum(p.request_rate for p in ready_pods)
    svc.error_rate = sum(p.error_rate for p in ready_pods) / len(ready_pods)
    svc.latency_p50 = sum(p.latency_p50 for p in ready_pods) / len(ready_pods)
    svc.latency_p95 = sum(p.latency_p95 for p in ready_pods) / len(ready_pods)
    svc.network_error_rate = sum(p.network_error_rate for p in ready_pods) / len(ready_pods)
