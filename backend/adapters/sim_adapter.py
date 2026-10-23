"""
adapters/sim_adapter.py — ClusterAdapter backed by the digital twin.

The pipeline and tests only interact with the twin through this class.
"""

from __future__ import annotations

from typing import Any

from adapters.base import ClusterAdapter
from twin.cluster import ClusterSnapshot, ClusterState, EventReason, PodPhase, Pod
from twin.faults import FaultRegistry
from twin.workload import LoadGenerator, SERVICE_BASELINE_MEM_FRACTION
from twin.scheduler import reschedule_pods_from_node


class SimAdapter(ClusterAdapter):
    """
    Wraps the digital twin's ClusterState and exposes the ClusterAdapter interface.
    Accepts the twin's mutable state by reference — mutations are visible immediately.
    """

    def __init__(self, state: ClusterState, faults: FaultRegistry,
                 load_gen: LoadGenerator) -> None:
        self._state = state
        self._faults = faults
        self._load_gen = load_gen

    def get_snapshot(self) -> ClusterSnapshot:
        return self._state.snapshot()

    def get_events(self) -> list[dict]:
        events = self._state.drain_events()
        return [e.model_dump() for e in events]

    def dry_run(self, action: str, target: str,
                params: dict[str, Any]) -> dict:
        """Validate action without executing. Returns affected pods."""
        state = self._state
        affected: list[str] = []
        ok = True
        detail = ""

        if action == "restart":
            pods = [p for p in state.pods.values()
                    if p.service == target and p.phase == PodPhase.Running]
            if not pods:
                ok = False
                detail = f"No running pods for service {target}"
            else:
                affected = [pods[0].id]  # restart one pod at a time per guardrails
                detail = f"Would restart pod {pods[0].id}"

        elif action == "scale_up":
            svc = state.services.get(target)
            if svc is None:
                ok = False
                detail = f"Service {target} not found"
            elif svc.replicas >= svc.hpa_max_replicas:
                ok = False
                detail = f"Service {target} already at replica ceiling ({svc.hpa_max_replicas})"
            else:
                detail = f"Would scale {target} from {svc.replicas} to {svc.replicas + 1}"

        elif action == "rollback":
            svc = state.services.get(target)
            if svc is None:
                ok = False
                detail = f"Service {target} not found"
            elif svc.revision == "v1":
                ok = False
                detail = f"Service {target} already at v1 — no previous revision"
            else:
                detail = f"Would rollback {target} from {svc.revision} to v1"

        elif action == "recreate":
            pods = [p for p in state.pods.values() if p.service == target]
            affected = [pods[0].id] if pods else []
            detail = f"Would recreate pod {affected[0]}" if affected else f"No pods for {target}"
            ok = bool(affected)

        elif action == "reschedule":
            # target is a node id for reschedule
            pods = [p for p in state.pods.values() if p.node == target]
            affected = [p.id for p in pods]
            detail = f"Would reschedule {len(affected)} pods from node {target}"

        elif action in ("no_op", "escalate"):
            detail = f"Action {action} — no cluster changes"

        else:
            ok = False
            detail = f"Unknown action: {action}"

        return {"ok": ok, "detail": detail, "affected_pods": affected}

    def execute(self, action: str, target: str,
                params: dict[str, Any]) -> dict:
        """Execute a healing action against the twin."""
        state = self._state
        sim_time = state.sim_time

        if action == "restart":
            return self._restart(target, sim_time)
        elif action == "scale_up":
            return self._scale_up(target, sim_time, params)
        elif action == "rollback":
            return self._rollback(target, sim_time)
        elif action == "recreate":
            return self._recreate(target, sim_time)
        elif action == "reschedule":
            return self._reschedule(target, sim_time)
        elif action in ("no_op", "escalate"):
            return {"ok": True, "detail": f"{action} — no cluster change"}
        else:
            return {"ok": False, "detail": f"Unknown action: {action}"}

    # ── Action implementations ────────────────────────────────────────────────

    def _restart(self, service_id: str, sim_time: float) -> dict:
        """Restart one pod of a service (per guardrail: one at a time)."""
        pods = [p for p in self._state.pods.values()
                if p.service == service_id and p.phase == PodPhase.Running]
        if not pods:
            return {"ok": False, "detail": f"No running pods for {service_id}"}
        pod = pods[0]
        pod.phase = PodPhase.Pending
        pod.ready = False
        pod.backoff_until = 0.0
        pod.mem_bytes = int(pod.mem_limit_bytes *
                            SERVICE_BASELINE_MEM_FRACTION.get(service_id, 0.25))
        pod.restarts += 1
        self._state.emit_event(EventReason.ActionExecuted, pod.id,
                               f"Restarted pod {pod.id} (action: restart)",
                               {"action": "restart", "service": service_id})
        return {"ok": True, "detail": f"Restarted pod {pod.id}", "pod_id": pod.id}

    def _scale_up(self, service_id: str, sim_time: float,
                  params: dict) -> dict:
        """Add one replica to a service (bounded by ceiling)."""
        svc = self._state.services.get(service_id)
        if svc is None:
            return {"ok": False, "detail": f"Service {service_id} not found"}
        if svc.replicas >= svc.hpa_max_replicas:
            return {"ok": False, "detail": f"{service_id} at replica ceiling"}

        mem_limit = svc.mem_limit_per_pod
        existing_ids = {p.id for p in self._state.pods.values()}
        idx = svc.replicas
        while f"{service_id}-{idx}" in existing_ids:
            idx += 1
        pod_id = f"{service_id}-{idx}"

        from twin.hpa import _least_loaded_nodes
        node_id = (_least_loaded_nodes(self._state, 1) or ["node-1"])[0]
        new_pod = Pod(
            id=pod_id, service=service_id, node=node_id,
            mem_bytes=int(mem_limit * SERVICE_BASELINE_MEM_FRACTION.get(service_id, 0.25)),
            mem_limit_bytes=mem_limit,
            phase=PodPhase.Pending, ready=False,
        )
        self._state.add_pod(new_pod)
        svc.replicas += 1
        self._state.emit_event(EventReason.ScaledUp, service_id,
                               f"Scaled {service_id} to {svc.replicas} replicas (action: scale_up)",
                               {"action": "scale_up", "new_replicas": svc.replicas})
        return {"ok": True, "detail": f"Scaled {service_id} to {svc.replicas}",
                "pod_id": pod_id, "new_replicas": svc.replicas}

    def _rollback(self, service_id: str, sim_time: float) -> dict:
        """Rollback service to previous revision (simulated as v1)."""
        svc = self._state.services.get(service_id)
        if svc is None:
            return {"ok": False, "detail": f"Service {service_id} not found"}
        old_rev = svc.revision
        svc.revision = "v1"
        svc.last_revision_time = sim_time
        # Reset error rate on all pods
        for pod in self._state.pods.values():
            if pod.service == service_id:
                pod.revision = "v1"
                pod.error_rate = max(0.0, pod.error_rate - 0.3)
        self._state.emit_event(EventReason.Rollback, service_id,
                               f"Rolled back {service_id} from {old_rev} to v1",
                               {"action": "rollback", "from": old_rev, "to": "v1"})
        return {"ok": True, "detail": f"Rolled back {service_id}: {old_rev}→v1"}

    def _recreate(self, service_id: str, sim_time: float) -> dict:
        """Delete and recreate one pod of a service."""
        pods = [p for p in self._state.pods.values() if p.service == service_id]
        if not pods:
            return {"ok": False, "detail": f"No pods for {service_id}"}
        pod = pods[0]
        pod_id = pod.id
        pod.phase = PodPhase.Terminating
        pod.ready = False
        # Reschedule immediately to a healthy node
        ready_nodes = self._state.nodes_ready()
        if ready_nodes:
            pod.node = ready_nodes[0].id
        pod.phase = PodPhase.Pending
        pod.backoff_until = 0.0
        pod.mem_bytes = int(pod.mem_limit_bytes *
                            SERVICE_BASELINE_MEM_FRACTION.get(service_id, 0.25))
        pod.restarts += 1
        self._state.emit_event(EventReason.ActionExecuted, pod_id,
                               f"Recreated pod {pod_id}",
                               {"action": "recreate", "service": service_id})
        return {"ok": True, "detail": f"Recreated pod {pod_id}"}

    def _reschedule(self, node_id: str, sim_time: float) -> dict:
        """Reschedule all pods from a failed node to healthy nodes."""
        rescheduled = reschedule_pods_from_node(self._state, node_id)
        return {
            "ok": True,
            "detail": f"Rescheduled {len(rescheduled)} pods from {node_id}",
            "rescheduled_pods": rescheduled,
        }
