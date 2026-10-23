"""
twin/scheduler.py — Simulated Kubernetes scheduler.

Handles: reschedule pods from failed nodes, pod eviction, Unknown→Pending→Running.
Also used by the executor for the `reschedule` healing action.
"""

from __future__ import annotations

from twin.cluster import ClusterState, EventReason, NodeCondition, PodPhase
from twin.dynamics import NODE_EVICTION_TIMEOUT_S


def reschedule_pods_from_node(state: ClusterState, node_id: str) -> list[str]:
    """
    Move all non-running pods from a failed node to available nodes.
    Returns list of rescheduled pod ids.
    Called by the executor (reschedule action) and by the sim loop after eviction timeout.
    """
    available_nodes = [n for n in state.nodes.values()
                       if n.id != node_id and n.condition == NodeCondition.Ready]
    if not available_nodes:
        return []  # no available nodes — pods stay Unknown

    rescheduled: list[str] = []
    pod_idx = 0
    for pod in state.pods.values():
        if pod.node != node_id:
            continue
        if pod.phase in (PodPhase.Unknown, PodPhase.Pending, PodPhase.Failed):
            target_node = available_nodes[pod_idx % len(available_nodes)]
            pod_idx += 1
            pod.node = target_node.id
            pod.phase = PodPhase.Pending
            pod.ready = False
            rescheduled.append(pod.id)
            state.emit_event(
                EventReason.Rescheduled, pod.id,
                f"Pod {pod.id} rescheduled to {target_node.id} after node {node_id} failure",
                {"from_node": node_id, "to_node": target_node.id},
            )

    return rescheduled


def tick_pending_pods(state: ClusterState, sim_time: float,
                      startup_delay: float = 15.0) -> None:
    """
    Advance Pending pods toward Running.
    Pods that have been Pending for startup_delay seconds go Running.
    Approximate Kubernetes rolling startup — no precise timestamps tracked,
    so we use a fixed delay (good enough for demo).
    """
    for pod in state.pods.values():
        if pod.phase == PodPhase.Pending:
            # Approximate: use backoff_until as "pending_since + startup_delay"
            # We reuse backoff_until as "ready_at" for Pending pods
            if pod.backoff_until == 0.0:
                pod.backoff_until = sim_time + startup_delay
            elif sim_time >= pod.backoff_until:
                pod.phase = PodPhase.Running
                pod.ready = True
                pod.backoff_until = 0.0
                svc = state.services.get(pod.service)
                if svc:
                    svc.ready_replicas = sum(
                        1 for p in state.pods.values()
                        if p.service == pod.service
                        and p.phase == PodPhase.Running and p.ready
                    )
                state.emit_event(
                    EventReason.Started, pod.id,
                    f"Pod {pod.id} started on {pod.node}",
                )


def evict_unknown_pods(state: ClusterState, sim_time: float) -> list[str]:
    """
    Evict Unknown pods that have been Unknown for > NODE_EVICTION_TIMEOUT_S.
    Returns list of evicted pod ids.
    Scheduler will subsequently reschedule them to healthy nodes.
    """
    evicted: list[str] = []
    for pod in state.pods.values():
        if pod.phase == PodPhase.Unknown:
            node = state.nodes.get(pod.node)
            if node is None or node.ready:
                continue
            elapsed = sim_time - (node.failed_at or sim_time)
            if elapsed >= NODE_EVICTION_TIMEOUT_S:
                pod.phase = PodPhase.Pending
                pod.ready = False
                evicted.append(pod.id)
    return evicted
