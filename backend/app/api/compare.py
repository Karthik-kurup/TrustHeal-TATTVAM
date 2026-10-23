"""
app/api/compare.py — Compare Lab endpoints.

Runs two or three arms of the same scenario in lockstep:
  A0: Kubernetes defaults only (HPA + restart-on-crash, no TrustHeal)
  A1: Rule-based (same guardrails, threshold rules, no ML, no gate)
  A2: Full TrustHeal

Each arm runs a headless SimLoop for a fixed number of sim-seconds,
sharing the same seed and fault schedule. Results are stored in memory.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from twin.faults import FaultKind

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Arm definitions ────────────────────────────────────────────────────────────

class ArmMode(str, Enum):
    k8s_defaults   = "k8s_defaults"    # A0: no TrustHeal
    rule_based     = "rule_based"      # A1: threshold rules only
    full_trustheal = "full_trustheal"  # A2: full TrustHeal pipeline


@dataclass
class ArmResult:
    mode: str
    mttr_seconds: float | None        # mean TTR (sim-s) for resolved incidents
    resolved_count: int
    escalated_count: int
    unsafe_actions: int               # actions that made things worse
    total_actions: int
    metric_series: dict[str, list[dict]]  # service -> [{sim_time, value}...]
    incidents: list[dict]
    elapsed_wall_seconds: float


@dataclass
class CompareRun:
    run_id: str
    seed: int
    duration_sim_seconds: int
    fault_schedule: list[dict]
    requested_arms: list[str] = field(
        default_factory=lambda: ["k8s_defaults", "rule_based", "full_trustheal"]
    )
    status: str = "pending"           # pending / running / done / error
    error: str = ""
    arms: dict[str, ArmResult] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# In-memory store of compare runs
_compare_runs: dict[str, CompareRun] = {}


# ── Request / response models ──────────────────────────────────────────────────

class ScheduledFault(BaseModel):
    kind: FaultKind
    target: str = Field(min_length=1)
    at: int = Field(default=60, ge=0)
    duration: int = Field(default=300, gt=0)
    intensity: float = Field(default=1.0, ge=0)


class CompareRunRequest(BaseModel):
    seed: int = 42
    duration_sim_seconds: int = Field(default=900, ge=1)
    fault_schedule: list[ScheduledFault] = Field(default_factory=list)
    arms: list[ArmMode] = Field(
        default_factory=lambda: [
            ArmMode.k8s_defaults,
            ArmMode.rule_based,
            ArmMode.full_trustheal,
        ],
        min_length=1,
    )


# ── Headless arm runner ────────────────────────────────────────────────────────

def _run_arm(
    mode: str,
    seed: int,
    duration: int,
    fault_schedule: list[dict],
) -> ArmResult:
    """
    Run one arm headlessly (no WS broadcasting, no UI).
    Uses the SimLoop with headless=True to suppress broadcasts.
    """
    from twin.cluster import build_shop_cluster
    from twin.workload import LoadGenerator, RngStreams
    from twin.faults import FaultRegistry, FaultSpec, FaultKind, OnsetShape
    from twin.dynamics import step as dynamics_step
    from twin.scheduler import tick_pending_pods, evict_unknown_pods
    from twin.hpa import HpaController
    from adapters.sim_adapter import SimAdapter
    from telemetry.store import TelemetryStore
    from telemetry.features import FeatureEngine
    from telemetry.health import HealthChecker, HealthStatus

    TELEMETRY_INTERVAL = 15

    cluster_state = build_shop_cluster()
    rng = RngStreams.from_seed(seed)
    load_gen = LoadGenerator(rng=rng)
    hpa = HpaController()
    faults = FaultRegistry()
    telemetry = TelemetryStore()
    feature_engine = FeatureEngine()
    health_checker = HealthChecker()
    adapter = SimAdapter(cluster_state, faults, load_gen)

    # For full TrustHeal and rule-based, set up pipeline components
    incident_store_obj = None
    executor_obj = None
    if mode in ("full_trustheal", "rule_based"):
        from store.incidents import IncidentStore
        from store.audit import AuditLog
        from healing.guardrails import GuardrailManager
        from healing.executor import Executor
        incident_store_obj = IncidentStore()
        audit = AuditLog()
        guardrails = GuardrailManager()
        executor_obj = Executor(adapter, guardrails, audit)

    # Schedule faults
    fault_ids_by_time: dict[int, list[dict]] = {}
    for f in fault_schedule:
        at = int(f.get("at", 60))
        fault_ids_by_time.setdefault(at, []).append(f)

    # Tracking
    unsafe_actions = 0
    total_actions = 0
    metric_series: dict[str, list[dict]] = {}
    incident_records: list[dict] = []

    wall_start = time.perf_counter()

    for t in range(0, duration, 1):
        # Inject scheduled faults
        for fi in fault_ids_by_time.get(t, []):
            spec = FaultSpec(
                kind=FaultKind(fi["kind"]),
                target=fi["target"],
                intensity=float(fi.get("intensity", 1.0)),
                duration=int(fi.get("duration", 300)),
            )
            faults.inject(spec, float(t))

        # Physics step
        dynamics_step(cluster_state, load_gen, faults)
        sim_time = cluster_state.sim_time
        tick_pending_pods(cluster_state, sim_time)
        evict_unknown_pods(cluster_state, sim_time)
        faults.tick(sim_time)

        # Telemetry every 15 sim-s
        if sim_time % TELEMETRY_INTERVAL == 0:
            snap = cluster_state.snapshot()
            telemetry.record(snap)
            hpa.tick(cluster_state, sim_time)

            for svc_id in telemetry.services():
                fv = feature_engine.compute(svc_id, telemetry)
                if fv is None:
                    continue

                # Record metric series
                if svc_id not in metric_series:
                    metric_series[svc_id] = []
                metric_series[svc_id].append({
                    "sim_time": sim_time,
                    "cpu": fv.cpu_util_mean,
                    "error_rate": fv.error_rate,
                    "latency_p95": fv.latency_p95,
                    "request_rate": fv.request_rate,
                })

                health_events = health_checker.evaluate(fv)
                if mode == "k8s_defaults":
                    continue   # No healing — just track

                for he in health_events:
                    if he.new_status == HealthStatus.BAD and incident_store_obj is not None:
                        if not incident_store_obj.get_open(svc_id):
                            incident_store_obj.open(svc_id, sim_time, he.reason)

                if incident_store_obj is None:
                    continue

                inc = incident_store_obj.get_open(svc_id)
                if inc is None:
                    continue

                if mode == "rule_based":
                    # Simple threshold: restart if error_rate high, scale_up if CPU high
                    from diagnosis.evidence_rules import diagnose as ev_diagnose
                    ev_label, ev_conf = ev_diagnose(fv), None
                    from healing.runbook import next_action
                    step_r = next_action(ev_label, svc_id, inc.runbook_rung)
                    if step_r.action not in ("escalate", "no_op") and executor_obj:
                        rec = executor_obj.execute(
                            step_r.action, step_r.target, svc_id, "R1",
                            sim_time, inc.id, "ASK", "S3", inc.runbook_rung,
                        )
                        total_actions += 1
                        if not rec.result_ok:
                            unsafe_actions += 1
                        inc.runbook_rung += 1

                elif mode == "full_trustheal":
                    from diagnosis.ensemble import diagnose as ens_diagnose
                    from trust import gate as trust_gate
                    from healing.runbook import next_action
                    ens = ens_diagnose(fv)
                    step_r = next_action(ens.label, svc_id, inc.runbook_rung)
                    gate_r = trust_gate.evaluate(
                        fv=fv, ensemble=ens,
                        health_status=health_checker.status(svc_id),
                        proposed_action=step_r.action,
                        proposed_target=step_r.target,
                        autonomy_stage="S3",
                        kill_switch=False,
                    )
                    if gate_r.decision.value == "AUTO" and executor_obj:
                        rec = executor_obj.execute(
                            step_r.action, step_r.target, svc_id,
                            gate_r.risk_class, sim_time, inc.id,
                            gate_r.decision.value, "S3", inc.runbook_rung,
                        )
                        total_actions += 1
                        if not rec.result_ok:
                            unsafe_actions += 1
                        inc.runbook_rung += 1

    wall_elapsed = time.perf_counter() - wall_start

    # Compute MTTR from incident store
    if incident_store_obj:
        for svc_id in list(incident_store_obj._open.keys()):
            incident_store_obj.close(svc_id, float(duration), resolved=False)
        all_incs = incident_store_obj.list_all(200)
        resolved = [i for i in all_incs if i.status.value == "RESOLVED" and i.time_to_recover]
        mttr = (sum(i.time_to_recover for i in resolved) / len(resolved)) if resolved else None
        resolved_count = len(resolved)
        escalated_count = len([i for i in all_incs if i.status.value == "ESCALATED"])
        incident_records = [
            {"id": i.id, "service": i.service, "status": i.status.value,
             "ttr": i.time_to_recover}
            for i in all_incs
        ]
    else:
        mttr = None
        resolved_count = 0
        escalated_count = 0

    return ArmResult(
        mode=mode,
        mttr_seconds=mttr,
        resolved_count=resolved_count,
        escalated_count=escalated_count,
        unsafe_actions=unsafe_actions,
        total_actions=total_actions,
        metric_series=metric_series,
        incidents=incident_records,
        elapsed_wall_seconds=wall_elapsed,
    )


# ── Background compare task ────────────────────────────────────────────────────

async def _run_compare(run_id: str) -> None:
    run = _compare_runs[run_id]
    run.status = "running"
    try:
        results = await asyncio.gather(*[
            asyncio.to_thread(
                _run_arm,
                arm,
                run.seed,
                run.duration_sim_seconds,
                run.fault_schedule,
            )
            for arm in run.requested_arms
        ])
        run.arms = {r.mode: r for r in results}
        run.status = "done"
    except Exception as exc:
        run.status = "error"
        run.error = str(exc)
        logger.exception("Compare run %s failed", run_id)


# ── REST endpoints ─────────────────────────────────────────────────────────────

@router.get("/compare/status")
async def compare_status():
    """Return a list of all compare runs."""
    return [
        {
            "run_id": r.run_id,
            "status": r.status,
            "seed": r.seed,
            "duration": r.duration_sim_seconds,
            "created_at": r.created_at,
            "arm_count": len(r.arms),
        }
        for r in _compare_runs.values()
    ]


@router.post("/compare/run")
async def start_compare_run(body: CompareRunRequest):
    """Start a new lockstep compare run."""
    run_id = str(uuid.uuid4())[:8]
    run = CompareRun(
        run_id=run_id,
        seed=body.seed,
        duration_sim_seconds=body.duration_sim_seconds,
        fault_schedule=[fault.model_dump() for fault in body.fault_schedule],
        requested_arms=[arm.value for arm in body.arms],
    )
    _compare_runs[run_id] = run
    asyncio.create_task(_run_compare(run_id))
    return {"run_id": run_id, "status": "running"}


@router.get("/compare/{run_id}")
async def get_compare_run(run_id: str):
    """Get results of a compare run."""
    run = _compare_runs.get(run_id)
    if not run:
        raise HTTPException(404, f"Compare run {run_id!r} not found")

    if run.status != "done":
        return {"run_id": run_id, "status": run.status, "error": run.error}

    arms_out = {}
    for mode, arm in run.arms.items():
        arms_out[mode] = {
            "mode": arm.mode,
            "mttr_seconds": arm.mttr_seconds,
            "resolved_count": arm.resolved_count,
            "escalated_count": arm.escalated_count,
            "unsafe_actions": arm.unsafe_actions,
            "total_actions": arm.total_actions,
            "incident_count": len(arm.incidents),
            "elapsed_wall_seconds": arm.elapsed_wall_seconds,
        }

    return {
        "run_id": run_id,
        "status": run.status,
        "seed": run.seed,
        "duration": run.duration_sim_seconds,
        "arms": arms_out,
    }


@router.get("/compare/{run_id}/metrics")
async def get_compare_metrics(run_id: str, service: str = "frontend", metric: str = "error_rate"):
    """Get time series for a metric from all arms of a compare run."""
    run = _compare_runs.get(run_id)
    if not run:
        raise HTTPException(404, f"Compare run {run_id!r} not found")
    if run.status != "done":
        raise HTTPException(409, f"Run {run_id} is {run.status}")

    result = {}
    for mode, arm in run.arms.items():
        series = arm.metric_series.get(service, [])
        result[mode] = [{"sim_time": p["sim_time"], "value": p.get(metric, 0)} for p in series]
    return result
