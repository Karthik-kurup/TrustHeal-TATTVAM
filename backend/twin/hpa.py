"""
twin/hpa.py — Simulated Horizontal Pod Autoscaler.

Watches average CPU utilisation across a service's pods and scales
replicas up/down toward the target, bounded by min/max and replica ceiling.
"""

from __future__ import annotations

from twin.cluster import ClusterState, EventReason, PodPhase, Pod
from twin.workload import SERVICE_BASELINE_MEM_FRACTION


_SCALE_COOLDOWN_S: float = 90.0   # sim-seconds between scale events
_STABILISATION_WINDOW: int = 3    # number of consecutive ticks above threshold


class HpaController:
    """
    Runs once per telemetry tick (every 15 sim-s).
    Tracks cooldown and stabilisation windows per service.
    """

    def __init__(self) -> None:
        self._last_scale_time: dict[str, float] = {}
        self._above_count: dict[str, int] = {}   # ticks above CPU target
        self._below_count: dict[str, int] = {}   # ticks below scale-down threshold

    def tick(self, state: ClusterState, sim_time: float) -> None:
        for svc_id, svc in state.services.items():
            if not svc.hpa_enabled:
                svc.hpa_desired = None
                continue
            self._evaluate(state, svc_id, sim_time)

    def _evaluate(self, state: ClusterState, svc_id: str, sim_time: float) -> None:
        svc = state.services[svc_id]
        ready_pods = state.ready_pods_for_service(svc_id)
        if not ready_pods:
            svc.hpa_desired = svc.replicas
            return

        avg_cpu = sum(p.cpu_util for p in ready_pods) / len(ready_pods)
        cooldown_ok = (
            svc_id not in self._last_scale_time or
            sim_time - self._last_scale_time[svc_id] >= _SCALE_COOLDOWN_S
        )

        # Scale-up: average CPU > target for stabilisation window ticks
        if avg_cpu > svc.hpa_target_cpu:
            self._above_count[svc_id] = self._above_count.get(svc_id, 0) + 1
            self._below_count[svc_id] = 0
            desired = min(svc.hpa_max_replicas,
                          round(svc.replicas * avg_cpu / svc.hpa_target_cpu))
            svc.hpa_desired = desired
            if (desired > svc.replicas and cooldown_ok and
                    self._above_count.get(svc_id, 0) >= _STABILISATION_WINDOW):
                _scale(state, svc_id, desired, sim_time)
                self._last_scale_time[svc_id] = sim_time
                self._above_count[svc_id] = 0
        elif avg_cpu < svc.hpa_target_cpu * 0.5:
            # Scale-down: below 50% of target
            self._below_count[svc_id] = self._below_count.get(svc_id, 0) + 1
            self._above_count[svc_id] = 0
            desired = max(svc.hpa_min_replicas,
                          round(svc.replicas * avg_cpu / svc.hpa_target_cpu))
            svc.hpa_desired = desired
            if (desired < svc.replicas and cooldown_ok and
                    self._below_count.get(svc_id, 0) >= _STABILISATION_WINDOW):
                _scale(state, svc_id, desired, sim_time)
                self._last_scale_time[svc_id] = sim_time
                self._below_count[svc_id] = 0
        else:
            self._above_count[svc_id] = 0
            self._below_count[svc_id] = 0
            svc.hpa_desired = svc.replicas


def _scale(state: ClusterState, svc_id: str, desired: int, sim_time: float) -> None:
    """Add or remove pods to reach desired replica count."""
    svc = state.services[svc_id]
    current = svc.replicas
    mem_limit = svc.mem_limit_per_pod

    if desired > current:
        # Scale up — add pods on least-loaded nodes
        node_ids = _least_loaded_nodes(state, desired - current)
        for i, node_id in enumerate(node_ids):
            pod_id = f"{svc_id}-{current + i}"
            # Avoid id collisions by scanning for unused ids
            existing_ids = {p.id for p in state.pods.values()}
            idx = current + i
            while f"{svc_id}-{idx}" in existing_ids:
                idx += 1
            pod_id = f"{svc_id}-{idx}"
            new_pod = Pod(
                id=pod_id,
                service=svc_id,
                node=node_id,
                mem_bytes=int(mem_limit * SERVICE_BASELINE_MEM_FRACTION.get(svc_id, 0.25)),
                mem_limit_bytes=mem_limit,
                phase=PodPhase.Pending,
                ready=False,
            )
            state.add_pod(new_pod)
            state.emit_event(EventReason.ScaledUp, svc_id,
                             f"HPA scaled {svc_id} {current}→{desired} (added {pod_id})")
        svc.replicas = desired

    elif desired < current:
        # Scale down — terminate most recently added pods
        running_pods = sorted(
            [p for p in state.pods.values()
             if p.service == svc_id and p.phase == PodPhase.Running],
            key=lambda p: p.id, reverse=True,
        )
        to_remove = current - desired
        for pod in running_pods[:to_remove]:
            pod.phase = PodPhase.Terminating
            pod.ready = False
            state.emit_event(EventReason.ScaledDown, svc_id,
                             f"HPA scaled {svc_id} {current}→{desired} (removed {pod.id})")
        # Remove Terminating pods immediately (simplified)
        terminating = [p.id for p in state.pods.values()
                       if p.service == svc_id and p.phase == PodPhase.Terminating]
        for pid in terminating:
            del state.pods[pid]
        svc.replicas = desired


def _least_loaded_nodes(state: ClusterState, count: int) -> list[str]:
    """Return the `count` most-available node ids (by free CPU)."""
    ready = sorted(
        state.nodes_ready(),
        key=lambda n: n.cpu_used / max(n.cpu_capacity, 0.001),
    )
    node_ids = [n.id for n in ready]
    result = []
    for i in range(count):
        result.append(node_ids[i % len(node_ids)] if node_ids else "node-1")
    return result
