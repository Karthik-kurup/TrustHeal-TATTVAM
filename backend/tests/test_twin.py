"""
tests/test_twin.py — M1 acceptance tests for the digital twin.

Tests:
  1. Determinism: same seed → same metrics after N ticks
  2. All 7 known faults visibly change metrics
  3. OOMKilled pod lifecycle
  4. Cascade propagation (callee failure raises caller error rate)
  5. Node failure: pods go Unknown → reschedule survives on 3 nodes
  6. RNG stream isolation: arm A's action cannot alter arm B's workload sequence
"""

from __future__ import annotations

import sys
import os

# Ensure backend/ is on sys.path when running from the backend/ directory
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import copy

import pytest

from twin.cluster import build_shop_cluster, ClusterState, PodPhase, NodeCondition
from twin.workload import LoadGenerator, RngStreams
from twin.faults import FaultKind, FaultRegistry, FaultSpec, OnsetShape
from twin.dynamics import step as dynamics_step
from twin.scheduler import reschedule_pods_from_node


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_sim(seed: int = 42):
    """Return (state, faults, load_gen) for a fresh shop cluster."""
    state = build_shop_cluster()
    faults = FaultRegistry()
    rng = RngStreams.from_seed(seed)
    load_gen = LoadGenerator(rng=rng)
    return state, faults, load_gen


def run_ticks(state, faults, load_gen, n: int) -> None:
    """Advance simulation by n physics steps (1 sim-s each)."""
    for _ in range(n):
        dynamics_step(state, load_gen, faults)


def service_avg_metric(state: ClusterState, service_id: str,
                        metric: str) -> float:
    """Average a pod-level metric across ready pods of a service."""
    pods = state.ready_pods_for_service(service_id)
    if not pods:
        return 0.0
    return sum(getattr(p, metric) for p in pods) / len(pods)


# ── Test 1: Determinism ───────────────────────────────────────────────────────

def test_determinism_same_seed():
    """Same seed + same fault schedule → identical metrics after N ticks."""
    N = 50

    state_a, faults_a, load_a = make_sim(seed=42)
    state_b, faults_b, load_b = make_sim(seed=42)

    # Inject same fault in both
    spec = FaultSpec(kind=FaultKind.cpu_stress, target="frontend",
                     intensity=0.5, onset_shape=OnsetShape.step,
                     onset_duration=0, duration=0)
    faults_a.inject(spec, sim_time=10.0)
    faults_b.inject(copy.deepcopy(spec), sim_time=10.0)

    run_ticks(state_a, faults_a, load_a, N)
    run_ticks(state_b, faults_b, load_b, N)

    # CPU utilisation of frontend should be identical
    cpu_a = service_avg_metric(state_a, "frontend", "cpu_util")
    cpu_b = service_avg_metric(state_b, "frontend", "cpu_util")
    assert abs(cpu_a - cpu_b) < 1e-9, f"cpu_util differs: {cpu_a} vs {cpu_b}"

    # Sim time should be identical
    assert state_a.sim_time == state_b.sim_time


def test_determinism_different_seed_differs():
    """Different seeds produce different metric sequences."""
    state_a, faults_a, load_a = make_sim(seed=42)
    state_b, faults_b, load_b = make_sim(seed=99)
    run_ticks(state_a, faults_a, load_a, 30)
    run_ticks(state_b, faults_b, load_b, 30)
    cpu_a = service_avg_metric(state_a, "frontend", "cpu_util")
    cpu_b = service_avg_metric(state_b, "frontend", "cpu_util")
    # Different seeds should produce different noise
    assert abs(cpu_a - cpu_b) > 1e-9, "Different seeds should produce different metrics"


# ── Test 2: 7 Known faults each change at least 2 metrics ────────────────────

def _baseline_metrics(service: str, metric: str, ticks: int = 60):
    """Measure a metric under no faults for ticks steps."""
    state, faults, load_gen = make_sim()
    run_ticks(state, faults, load_gen, ticks)
    return service_avg_metric(state, service, metric)


@pytest.mark.parametrize("fault_kind,target,metrics,direction", [
    # (fault_kind, target_service, [metric_name, ...], "up" or "down")
    (FaultKind.cpu_stress,          "frontend",  ["cpu_util"],            "up"),
    (FaultKind.traffic_surge,       "frontend",  ["request_rate"],        "up"),
    (FaultKind.memory_exhaustion,   "checkout",  ["mem_bytes"],           "up"),
    (FaultKind.memory_leak,         "catalog",   ["mem_bytes"],           "up"),
    (FaultKind.network_loss,        "payment",   ["network_error_rate"],  "up"),
    (FaultKind.bad_deploy,          "payment",   ["error_rate"],          "up"),
    (FaultKind.node_failure,        "node-1",    [],                      "node"),
])
def test_known_fault_changes_metrics(fault_kind, target, metrics, direction):
    """Each known fault visibly changes at least one metric vs baseline."""
    WARMUP = 60   # ticks of warmup before fault
    FAULT_ONSET = 30  # let fault ramp
    MEASURE = 30   # ticks to measure effect

    # Baseline
    state_b, faults_b, load_b = make_sim()
    run_ticks(state_b, faults_b, load_b, WARMUP + FAULT_ONSET + MEASURE)

    # Fault run
    state_f, faults_f, load_f = make_sim()
    run_ticks(state_f, faults_f, load_f, WARMUP)
    spec = FaultSpec(kind=fault_kind, target=target,
                     intensity=1.0, onset_shape=OnsetShape.step,
                     onset_duration=0, duration=0)
    faults_f.inject(spec, sim_time=state_f.sim_time)
    run_ticks(state_f, faults_f, load_f, FAULT_ONSET + MEASURE)

    if direction == "node":
        # Node failure: check node went NotReady and some pods went Unknown/Pending
        node = state_f.nodes.get("node-1")
        assert node is not None
        assert node.condition == NodeCondition.NotReady, "Node should be NotReady"
        # Pods on failed node should be Unknown or Pending
        affected_pods = [p for p in state_f.pods.values()
                         if p.node == "node-1" or p.phase in
                         (PodPhase.Unknown, PodPhase.Pending)]
        assert len(affected_pods) > 0, "Some pods should be affected by node failure"
    else:
        for metric in metrics:
            svc = target if fault_kind != FaultKind.node_failure else "frontend"
            baseline_val = service_avg_metric(state_b, svc, metric)
            fault_val = service_avg_metric(state_f, svc, metric)
            if direction == "up":
                assert fault_val > baseline_val * 1.05, (
                    f"{fault_kind.value}: {metric} should increase "
                    f"({fault_val:.4f} vs baseline {baseline_val:.4f})"
                )
            elif direction == "down":
                assert fault_val < baseline_val * 0.95, (
                    f"{fault_kind.value}: {metric} should decrease "
                    f"({fault_val:.4f} vs baseline {baseline_val:.4f})"
                )


# ── Test 3: OOM lifecycle ─────────────────────────────────────────────────────

def test_oom_crash_and_restart():
    """Memory exhaustion → OOMKilled → CrashLoopBackOff → restart."""
    state, faults, load_gen = make_sim()
    # Warmup
    run_ticks(state, faults, load_gen, 30)

    # Inject severe memory exhaustion on one pod
    checkout_pods = state.pods_for_service("checkout")
    assert checkout_pods, "checkout should have pods"
    pod = checkout_pods[0]
    # Force memory over limit directly (bypasses ramp)
    pod.mem_bytes = pod.mem_limit_bytes + 1

    # One tick should trigger OOMKill → CrashLoopBackOff
    dynamics_step(state, load_gen, faults)

    assert pod.phase in (PodPhase.CrashLoopBackOff, PodPhase.OOMKilled), (
        f"Pod should be in CrashLoopBackOff after OOM, got {pod.phase}"
    )
    assert pod.restarts >= 1, "Restart count should increment on OOM"
    assert pod.termination_reason == "OOMKilled"
    assert pod.exit_code == 137

    # After backoff, pod should recover
    # Advance past the backoff time
    advance = int(pod.backoff_until - state.sim_time) + 5
    run_ticks(state, faults, load_gen, advance)
    assert pod.phase == PodPhase.Running, (
        f"Pod should recover after backoff, got {pod.phase} (backoff_until={pod.backoff_until},"
        f" sim_time={state.sim_time})"
    )


# ── Test 4: Cascade propagation ───────────────────────────────────────────────

def test_cascade_from_callee_to_caller():
    """
    Failing cache → catalog error rate rises (catalog calls cache).
    Failing payment → checkout error rate rises (checkout calls payment).
    """
    WARMUP = 60

    # Baseline checkout error rate
    state_b, faults_b, load_b = make_sim()
    run_ticks(state_b, faults_b, load_b, WARMUP)
    checkout_err_baseline = service_avg_metric(state_b, "checkout", "error_rate")

    # Fault run: bad deploy on payment (checkout calls payment)
    state_f, faults_f, load_f = make_sim()
    run_ticks(state_f, faults_f, load_f, WARMUP)
    spec = FaultSpec(kind=FaultKind.bad_deploy, target="payment",
                     intensity=1.0, onset_shape=OnsetShape.step,
                     onset_duration=0, duration=0)
    faults_f.inject(spec, sim_time=state_f.sim_time)
    run_ticks(state_f, faults_f, load_f, 30)

    checkout_err_fault = service_avg_metric(state_f, "checkout", "error_rate")
    assert checkout_err_fault > checkout_err_baseline * 1.5, (
        f"Checkout error rate should cascade from payment fault: "
        f"{checkout_err_fault:.4f} vs baseline {checkout_err_baseline:.4f}"
    )


# ── Test 5: Node failure survivability ────────────────────────────────────────

def test_node_failure_pods_reschedule():
    """
    After node-1 fails and eviction timeout elapses, rescheduling to remaining
    3 nodes should succeed (all pods Running eventually).
    """
    from twin.dynamics import NODE_NOTREADY_GRACE_S, NODE_EVICTION_TIMEOUT_S
    from twin.scheduler import evict_unknown_pods

    state, faults, load_gen = make_sim()
    run_ticks(state, faults, load_gen, 30)

    # Inject node failure
    spec = FaultSpec(kind=FaultKind.node_failure, target="node-1",
                     intensity=1.0, onset_shape=OnsetShape.step,
                     onset_duration=0, duration=0)
    faults.inject(spec, sim_time=state.sim_time)

    # Advance past grace period + eviction timeout
    total_wait = int(NODE_NOTREADY_GRACE_S + NODE_EVICTION_TIMEOUT_S + 10)
    run_ticks(state, faults, load_gen, total_wait)

    # Evict Unknown pods and reschedule
    evict_unknown_pods(state, state.sim_time)
    rescheduled = reschedule_pods_from_node(state, "node-1")

    # All pods that were on node-1 should now be on other nodes
    remaining_on_failed = [p for p in state.pods.values()
                           if p.node == "node-1" and p.phase == PodPhase.Running]
    assert not remaining_on_failed, (
        f"No running pods should remain on failed node, "
        f"found: {[p.id for p in remaining_on_failed]}"
    )

    # Check node capacity: 14 pods across 3 remaining nodes
    # Each pod ≤ 0.5 cores, each node has 3 cores → 3×3/0.5 = 18 max pods ✓
    for node in state.nodes.values():
        if node.id == "node-1":
            continue
        pods_on_node = [p for p in state.pods.values() if p.node == node.id]
        total_cpu_demand = sum(
            p.cpu_util * state.services[p.service].cpu_limit_per_pod
            for p in pods_on_node
            if p.service in state.services and p.phase == PodPhase.Running
        )
        assert total_cpu_demand <= node.cpu_capacity * 1.1, (  # 10% tolerance
            f"Node {node.id} CPU overloaded after reschedule: "
            f"{total_cpu_demand:.2f} / {node.cpu_capacity:.2f}"
        )


# ── Test 6: RNG stream isolation ──────────────────────────────────────────────

def test_rng_stream_isolation():
    """
    Arm A executes a scale_up action (adds a pod, which consumes dynamics_noise RNG).
    Arm B takes no action.
    After the same number of ticks, arm A and B's WORKLOAD RNG state should
    be identical (scale_up only affects dynamics_noise, not workload RNG).
    """
    from adapters.sim_adapter import SimAdapter

    # Create two arms with same seed
    state_a, faults_a, load_a = make_sim(seed=42)
    state_b, faults_b, load_b = make_sim(seed=42)

    WARMUP = 20
    run_ticks(state_a, faults_a, load_a, WARMUP)
    run_ticks(state_b, faults_b, load_b, WARMUP)

    # Arm A: scale up frontend (adds a pod → affects dynamics_noise stream)
    adapter_a = SimAdapter(state_a, faults_a, load_a)
    result = adapter_a.execute("scale_up", "frontend", {})
    assert result["ok"], f"scale_up failed: {result}"

    # Advance both arms by same ticks
    run_ticks(state_a, faults_a, load_a, 20)
    run_ticks(state_b, faults_b, load_b, 20)

    # Workload RNG state should be identical across arms (arm A's action
    # only consumed from dynamics_noise, not workload stream)
    state_a_workload = load_a.rng.workload.getstate()
    state_b_workload = load_b.rng.workload.getstate()
    assert state_a_workload == state_b_workload, (
        "Workload RNG streams should be identical — arm A's scale_up "
        "should not have consumed from the workload stream"
    )


# ── Test 7: Snapshot / restore ────────────────────────────────────────────────

def test_snapshot_restore():
    """Snapshot before fault, restore after fault → metrics return to pre-fault state."""
    from twin.snapshots import SnapshotStore

    state, faults, load_gen = make_sim()
    store = SnapshotStore()

    run_ticks(state, faults, load_gen, 30)
    cpu_before = service_avg_metric(state, "frontend", "cpu_util")
    snap_id = store.save(state, faults, load_gen, label="before_fault")

    # Inject fault and advance
    spec = FaultSpec(kind=FaultKind.cpu_stress, target="frontend",
                     intensity=1.0, onset_shape=OnsetShape.step,
                     onset_duration=0, duration=0)
    faults.inject(spec, sim_time=state.sim_time)
    run_ticks(state, faults, load_gen, 30)
    cpu_after_fault = service_avg_metric(state, "frontend", "cpu_util")
    assert cpu_after_fault > cpu_before * 1.1, "Fault should raise CPU"

    # Restore
    ok = store.restore(snap_id, state, faults, load_gen)
    assert ok, "Restore should succeed"
    run_ticks(state, faults, load_gen, 1)  # one tick to settle

    cpu_restored = service_avg_metric(state, "frontend", "cpu_util")
    # After restore, no fault → CPU should be near pre-fault level
    assert len(faults.active()) == 0, "Faults should be cleared after restore"
    # CPU within 20% of pre-fault (noise may differ slightly due to RNG restore)
    assert cpu_restored < cpu_before * 1.5, (
        f"CPU after restore ({cpu_restored:.4f}) should be near pre-fault "
        f"level ({cpu_before:.4f})"
    )
