"""
app/sim_loop.py - The asyncio simulation loop.

Physics: 1 sim-s per step.
Telemetry: sampled every 15 sim-s.
UI batching: flushed at ~10 Hz wall time (or immediately in headless mode).

Full pipeline per telemetry tick:
  1. Record telemetry snapshot
  2. Compute features
  3. Health check -> open/close incidents
  4. For each open incident:
       a. Run ensemble diagnosis
       b. Compute trust signals
       c. Trust gate -> AUTO/ASK/ESCALATE
       d. If AUTO: execute via guarded executor
       e. If VERIFYING: run verifier -> resolve or next rung
  5. Broadcast WS messages

Exposes:
  SimLoop.start()        -- start background asyncio task
  SimLoop.stop()         -- stop gracefully
  SimLoop.step_physics() -- advance 1 sim-s (test / step-on-demand)
  SimLoop.step_telemetry() -- advance 15 sim-s (test convenience)
  SimLoop.state          -- current ClusterState (read-only externally)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from twin.cluster import ClusterState, build_shop_cluster
from twin.workload import LoadGenerator, RngStreams
from twin.faults import FaultRegistry, FaultSpec
from twin.dynamics import step as dynamics_step
from twin.scheduler import tick_pending_pods, evict_unknown_pods, reschedule_pods_from_node
from twin.hpa import HpaController
from twin.snapshots import SnapshotStore
from adapters.sim_adapter import SimAdapter
from telemetry.store import TelemetryStore
from telemetry.features import FeatureEngine
from telemetry.health import HealthChecker, HealthStatus

from diagnosis.ensemble import diagnose as ensemble_diagnose, EnsembleResult
from store.incidents import (
    IncidentStore, Incident, IncidentStatus, GateDecision,
    DiagnosisResult, TrustSignals,
)
from store.audit import AuditLog
from healing.runbook import next_action as runbook_next
from healing.guardrails import GuardrailManager
from healing.executor import Executor
import healing.verifier as verifier
from trust import gate as trust_gate
from app.ws import WsHub, WsMessage, WsMessageType

logger = logging.getLogger(__name__)

TELEMETRY_INTERVAL = 15    # sim-s between pipeline samples
UI_BATCH_INTERVAL  = 0.10  # wall-s between WS flushes (~10 Hz)

# How many ticks diagnosis must be stable before gate decides
DIAGNOSIS_STABLE_TICKS = 3


class SimStatus(str, Enum):
    running = "running"
    paused  = "paused"
    stopped = "stopped"


@dataclass
class SimState:
    status: SimStatus = SimStatus.paused
    sim_time: float = 0.0
    speed: float = 10.0
    seed: int = 42


@dataclass
class ControlState:
    """Mutable control plane state — shared with API routes."""
    autonomy_stage: str = "S3"
    kill_switch: bool = False
    p_r1_threshold: float = 0.70
    p_r2_threshold: float = 0.85
    service_budget: int = 5
    cluster_budget: int = 20
    cooldown_seconds: float = 60.0


class SimLoop:
    """
    Central simulation controller. One instance per process (singleton via app state).
    """

    def __init__(self, seed: int = 42, speed: float = 10.0,
                 hub: WsHub | None = None) -> None:
        self.seed   = seed
        self._status = SimStatus.paused
        self._speed  = speed
        self._hub    = hub

        # Twin components
        self.cluster_state: ClusterState = build_shop_cluster()
        self.faults   = FaultRegistry()
        rng           = RngStreams.from_seed(seed)
        self.load_gen = LoadGenerator(rng=rng)
        self.hpa      = HpaController()
        self.snapshots = SnapshotStore()

        # Pipeline components
        self.telemetry      = TelemetryStore()
        self.feature_engine = FeatureEngine()
        self.health_checker = HealthChecker()

        # Adapter
        self.adapter = SimAdapter(self.cluster_state, self.faults, self.load_gen)

        # M5 pipeline components
        self.incident_store = IncidentStore()
        self.audit          = AuditLog()
        self.guardrails     = GuardrailManager()
        self.executor       = Executor(self.adapter, self.guardrails, self.audit)
        self.control        = ControlState()

        # Per-incident tracking (service_id -> last FeatureVector before action)
        self._pre_action_fv: dict[str, Any] = {}
        self._last_fv: dict[str, Any] = {}

        # Narration buffer
        self._narration: list[str] = []

        # Background task handle
        self._task: asyncio.Task | None = None
        self._resume_event = asyncio.Event()

        # Wall-time tracking for UI batching
        self._last_flush: float = 0.0
        self._pending_ws_messages: list[WsMessage] = []

    # ── Public properties ──────────────────────────────────────────────────────

    @property
    def status(self) -> SimStatus:
        return self._status

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = max(0.1, float(value))

    @property
    def sim_time(self) -> float:
        return self.cluster_state.sim_time

    @property
    def open_incidents(self) -> dict[str, dict]:
        """Backward-compatible dict view for existing API routes."""
        result: dict[str, dict] = {}
        for inc in self.incident_store.list_open():
            result[inc.service] = inc.model_dump()
        return result

    def get_state(self) -> SimState:
        return SimState(
            status=self._status,
            sim_time=self.sim_time,
            speed=self._speed,
            seed=self.seed,
        )

    def _sim_state_payload(self) -> dict:
        """Build the sim_state WS payload matching frontend SimStatePayload."""
        return {
            "status":         self._status.value,
            "speed":          self._speed,
            "sim_time":       self.sim_time,
            "seed":           self.seed,
            "autonomy_stage": self.control.autonomy_stage,
            "kill_switch":    self.control.kill_switch,
        }

    def _queue_sim_state(self) -> None:
        """Queue a sim_state WS broadcast."""
        self._queue_ws(WsMessage(
            type=WsMessageType.sim_state,
            sim_time=self.sim_time,
            incident_id=None,
            payload=self._sim_state_payload(),
        ))

    # ── Start / stop ───────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._status = SimStatus.running
        self._resume_event.set()
        self._task = asyncio.create_task(self._run(), name="sim_loop")
        logger.info("SimLoop started (seed=%d speed=%.1fx)", self.seed, self._speed)
        self._queue_sim_state()

    def stop(self) -> None:
        self._status = SimStatus.stopped
        self._resume_event.set()
        if self._task:
            self._task.cancel()

    def pause(self) -> None:
        self._status = SimStatus.paused
        self._resume_event.clear()
        self._queue_sim_state()
        logger.info("SimLoop paused at t=%.1f", self.sim_time)

    def resume(self) -> None:
        self._status = SimStatus.running
        self._resume_event.set()
        self._queue_sim_state()
        logger.info("SimLoop resumed at t=%.1f", self.sim_time)

    def reset(self, seed: int | None = None) -> None:
        if seed is not None:
            self.seed = seed
        was_running = self._status == SimStatus.running
        self.pause()
        self.cluster_state  = build_shop_cluster()
        self.faults.clear_all()
        rng           = RngStreams.from_seed(self.seed)
        self.load_gen = LoadGenerator(rng=rng)
        self.hpa      = HpaController()
        self.telemetry      = TelemetryStore()
        self.feature_engine = FeatureEngine()
        self.health_checker = HealthChecker()
        self.adapter  = SimAdapter(self.cluster_state, self.faults, self.load_gen)
        self.executor = Executor(self.adapter, self.guardrails, self.audit)
        self.incident_store.clear()
        self.guardrails.reset_all()
        self._pre_action_fv.clear()
        self._last_fv.clear()
        self._narration.clear()
        logger.info("SimLoop reset (seed=%d)", self.seed)
        if was_running:
            self.resume()

    # ── Step-on-demand (tests) ─────────────────────────────────────────────────

    def step_physics(self) -> None:
        """Advance exactly 1 sim-s. No sleep. For tests."""
        self._tick_physics()

    def step_telemetry(self) -> None:
        """Advance exactly 15 sim-s (one telemetry sample). For tests."""
        for _ in range(TELEMETRY_INTERVAL):
            self._tick_physics()

    # ── Fault / snapshot API ───────────────────────────────────────────────────

    def inject_fault(self, spec: FaultSpec) -> str:
        fault_id = self.faults.inject(spec, self.sim_time)
        self._narrate(f"Fault injected: {spec.kind.value} on {spec.target}")
        return fault_id

    def clear_fault(self, fault_id: str) -> bool:
        return self.faults.clear(fault_id)

    def save_snapshot(self, label: str = "") -> str:
        return self.snapshots.save(
            self.cluster_state, self.faults, self.load_gen, label=label
        )

    def restore_snapshot(self, snap_id: str) -> bool:
        ok = self.snapshots.restore(snap_id, self.cluster_state, self.faults, self.load_gen)
        if ok:
            self.telemetry      = TelemetryStore()
            self.feature_engine = FeatureEngine()
            self.health_checker = HealthChecker()
            self.incident_store.clear()
            self.guardrails.reset_all()
            self._pre_action_fv.clear()
            self._last_fv.clear()
            self._narrate(f"Restored snapshot {snap_id}")
        return ok

    # ── Internal loop ──────────────────────────────────────────────────────────

    async def _run(self) -> None:
        self._last_flush = time.monotonic()
        while self._status != SimStatus.stopped:
            if self._status == SimStatus.paused:
                await self._resume_event.wait()
                if self._status == SimStatus.stopped:
                    break
                self._last_flush = time.monotonic()

            self._tick_physics()

            now = time.monotonic()
            if now - self._last_flush >= UI_BATCH_INTERVAL:
                await self._flush_ws()
                self._last_flush = now

            sleep_s = 1.0 / self._speed
            await asyncio.sleep(sleep_s)

        logger.info("SimLoop stopped at t=%.1f", self.sim_time)

    def _tick_physics(self) -> None:
        sim_time = self.cluster_state.sim_time
        dynamics_step(self.cluster_state, self.load_gen, self.faults)
        tick_pending_pods(self.cluster_state, sim_time)
        evict_unknown_pods(self.cluster_state, sim_time)
        if int(self.cluster_state.sim_time) % TELEMETRY_INTERVAL == 0:
            self._tick_telemetry()

    def _tick_telemetry(self) -> None:
        """Process one telemetry sample (every 15 sim-s) — full pipeline."""
        events = self.cluster_state.drain_events()
        snap   = self.cluster_state.snapshot()
        from twin.cluster import ClusterSnapshot
        snap_with_events = ClusterSnapshot(
            sim_time=snap.sim_time,
            nodes=snap.nodes, pods=snap.pods, services=snap.services,
            events=events,
        )
        self.telemetry.record(snap_with_events)
        self.hpa.tick(self.cluster_state, snap.sim_time)

        # Per-service: features + health + pipeline
        for svc_id in self.telemetry.services():
            fv = self.feature_engine.compute(svc_id, self.telemetry)
            if fv is None:
                continue

            # Track last FV per service
            self._last_fv[svc_id] = fv

            # Health check
            health_events = self.health_checker.evaluate(fv)
            for he in health_events:
                if he.new_status == HealthStatus.BAD:
                    self._on_incident_open(svc_id, he, fv)
                elif he.new_status == HealthStatus.OK:
                    self._on_incident_close(svc_id, snap.sim_time)

            # Run pipeline for open incidents
            inc = self.incident_store.get_open(svc_id)
            if inc is not None:
                self._tick_incident_pipeline(inc, fv, snap.sim_time)
                self._queue_ws(WsMessage(
                    type=WsMessageType.incident_update,
                    sim_time=snap.sim_time,
                    incident_id=inc.id,
                    payload=inc.model_dump(),
                ))

        # Build per-service metrics dict (keyed by service id)
        svc_metrics: dict = {}
        for svc in snap.services:
            ready_pods = snap.ready_pods_for_service(svc.id)
            all_pods   = snap.pods_for_service(svc.id)
            cpu = (
                sum(p.cpu_util for p in ready_pods) / len(ready_pods)
                if ready_pods else 0.0
            )
            mem = (
                sum(p.mem_ratio for p in ready_pods) / len(ready_pods)
                if ready_pods else 0.0
            )
            restarts = sum(p.restarts for p in all_pods)
            svc_metrics[svc.id] = {
                "cpu":           cpu,
                "memory":        mem,
                "request_rate":  svc.request_rate,
                "error_rate":    svc.error_rate,
                "latency_p50":   svc.latency_p50 * 1000,   # s → ms
                "latency_p95":   svc.latency_p95 * 1000,   # s → ms
                "restarts":      restarts,
                "replicas":      svc.replicas,
                "network_errors": svc.network_error_rate,
            }

        # Build per-node metrics dict (keyed by node id)
        node_metrics: dict = {}
        for n in snap.nodes:
            cpu_frac = n.cpu_used / n.cpu_capacity if n.cpu_capacity > 0 else 0.0
            mem_frac = n.mem_used / n.mem_capacity if n.mem_capacity > 0 else 0.0
            node_metrics[n.id] = {
                "cpu":    cpu_frac,
                "memory": mem_frac,
                "status": n.condition.value,
            }

        # WS tick message — shape matches frontend TickPayload
        self._queue_ws(WsMessage(
            type=WsMessageType.tick,
            sim_time=snap.sim_time,
            incident_id=None,
            payload={
                "services":       svc_metrics,
                "nodes":          node_metrics,
                "open_incidents": [i.service for i in self.incident_store.list_open()],
                "active_faults":  [f.id for f in self.faults.active()],
            },
        ))

        # Sim state broadcast (keeps TopBar in sync every tick)
        self._queue_sim_state()

        # Narration flush
        if self._narration:
            for line in self._narration:
                self._queue_ws(WsMessage(
                    type=WsMessageType.narration,
                    sim_time=snap.sim_time,
                    incident_id=None,
                    payload={"text": line, "level": "info"},
                ))
            self._narration.clear()

    # ── Incident lifecycle ─────────────────────────────────────────────────────

    def _on_incident_open(self, service_id: str, health_event: Any,
                          fv: Any) -> None:
        inc = self.incident_store.open(service_id, self.sim_time,
                                       health_event.reason)
        # Attach ground truth from active faults if available
        for af in self.faults.active():
            if af.target == service_id or af.target.startswith(service_id):
                inc.ground_truth_fault = af.kind.value
                break
        self.audit.append(
            sim_time=self.sim_time,
            event_type="incident_opened",
            incident_id=inc.id,
            service=service_id,
            action="",
            target=service_id,
            risk_class="",
            gate_decision="",
            result="opened",
            detail=health_event.reason,
            autonomy_stage=self.control.autonomy_stage,
        )
        self._queue_ws(WsMessage(
            type=WsMessageType.incident_update,
            sim_time=self.sim_time,
            incident_id=inc.id,
            payload=inc.model_dump(),
        ))
        self._narrate(
            f"t={self.sim_time:.0f}s  {service_id}: incident opened -- "
            f"{health_event.reason[:80]}"
        )

    def _on_incident_close(self, service_id: str, sim_time: float) -> None:
        inc = self.incident_store.close(service_id, sim_time, resolved=True)
        if inc:
            self.audit.append(
                sim_time=sim_time,
                event_type="incident_closed",
                incident_id=inc.id,
                service=service_id,
                action="",
                target=service_id,
                risk_class="",
                gate_decision="",
                result="resolved",
                detail=f"Recovered; MTTR={inc.time_to_recover:.0f}s" if inc.time_to_recover else "Recovered",
                autonomy_stage=self.control.autonomy_stage,
            )
            self._queue_ws(WsMessage(
                type=WsMessageType.incident_update,
                sim_time=sim_time,
                incident_id=inc.id,
                payload=inc.model_dump(),
            ))
            self._narrate(
                f"t={sim_time:.0f}s  {service_id}: recovered"
                + (f" (MTTR {inc.time_to_recover:.0f}s)" if inc.time_to_recover else "")
            )

    # ── Per-incident pipeline ──────────────────────────────────────────────────

    def _tick_incident_pipeline(self, inc: Incident, fv: Any,
                                 sim_time: float) -> None:
        """Run one pipeline tick for an open incident."""

        # ── Verifying phase: check if action worked ──────────────────────────
        if inc.status == IncidentStatus.VERIFYING and inc.actions:
            last_action = inc.actions[-1]
            v_check = verifier.check(
                service_id=inc.service,
                health_checker=self.health_checker,
                action_executed_at=last_action.executed_at,
                sim_time=sim_time,
                last_fv=fv,
                pre_action_fv=self._pre_action_fv.get(inc.service),
            )
            if v_check.outcome.value == "PENDING":
                return   # still waiting

            inc.verification = v_check.result
            self._queue_ws(WsMessage(
                type=WsMessageType.verification,
                sim_time=sim_time,
                incident_id=inc.id,
                payload={
                    "outcome": v_check.outcome.value,
                    "detail": v_check.detail,
                    "result": v_check.result.model_dump() if v_check.result else None,
                },
            ))

            if v_check.outcome.value == "RESOLVED":
                inc.status = IncidentStatus.RESOLVED
                self._narrate(
                    f"t={sim_time:.0f}s  {inc.service}: action verified -- resolved"
                )
                return

            elif v_check.outcome.value == "WORSE":
                # Undo and escalate
                self.executor.undo(last_action, inc.service, sim_time,
                                   inc.id, self.control.autonomy_stage)
                last_action.undo_executed = True
                inc.status = IncidentStatus.ESCALATED
                self._narrate(
                    f"t={sim_time:.0f}s  {inc.service}: action made things worse -- "
                    f"undone and escalated"
                )
                return

            else:   # STILL_BAD -> next rung
                inc.status = IncidentStatus.OPEN
                inc.runbook_rung += 1
                self._narrate(
                    f"t={sim_time:.0f}s  {inc.service}: action did not resolve -- "
                    f"trying rung {inc.runbook_rung}"
                )

        # ── Waiting for human approval ───────────────────────────────────────
        approved_proposal: tuple[str, str] | None = None
        if inc.status == IncidentStatus.WAITING:
            if inc.rejected:
                inc.status = IncidentStatus.ESCALATED
                self._narrate(
                    f"t={sim_time:.0f}s  {inc.service}: action rejected -- escalated"
                )
                return
            if not inc.approved:
                return   # still waiting

            approved_proposal = (inc.action_name, inc.action_target)

        # ── Already acting/escalated/resolved -- skip ────────────────────────
        if inc.status in (IncidentStatus.ACTING, IncidentStatus.ESCALATED,
                          IncidentStatus.RESOLVED):
            return

        # ── Diagnose ─────────────────────────────────────────────────────────
        try:
            ens_result: EnsembleResult = ensemble_diagnose(fv)
        except Exception as exc:
            logger.warning("Ensemble diagnosis failed for %s: %s", inc.service, exc)
            ens_result = None  # type: ignore

        if ens_result is None:
            return

        # Track diagnosis stability (3 ticks of same label)
        inc.diagnosis_history.append(ens_result.label)
        if len(inc.diagnosis_history) > 10:
            inc.diagnosis_history.pop(0)

        if (len(inc.diagnosis_history) >= DIAGNOSIS_STABLE_TICKS and
                len(set(inc.diagnosis_history[-DIAGNOSIS_STABLE_TICKS:])) == 1):
            inc.diagnosis_stable_ticks = DIAGNOSIS_STABLE_TICKS
        else:
            inc.diagnosis_stable_ticks = 0

        # Update diagnosis in incident
        diag = DiagnosisResult(
            label=ens_result.label,
            confidence=ens_result.confidence,
            probs=ens_result.probs,
            mlp_label=ens_result.mlp.label if ens_result.mlp else "",
            xgb_label=ens_result.xgb.label if ens_result.xgb else "",
            evidence_label=ens_result.evidence.label if ens_result.evidence else "",
            top_features=ens_result.top_features,
            mlp_uncertainty=ens_result.mlp_uncertainty,
            abstained=ens_result.abstained,
        )
        inc.diagnosis = diag
        inc.status    = IncidentStatus.DIAGNOSED

        self._queue_ws(WsMessage(
            type=WsMessageType.diagnosis,
            sim_time=sim_time,
            incident_id=inc.id,
            payload={"diagnosis": diag.model_dump()},
        ))

        # Wait for diagnosis stability before gating
        if inc.diagnosis_stable_ticks < DIAGNOSIS_STABLE_TICKS:
            return

        # ── Trust Gate ───────────────────────────────────────────────────────
        inc.status = IncidentStatus.DECIDING

        # Determine current node for NODE_FAILURE
        failed_node = None
        if ens_result.label == "NODE_FAILURE":
            for node in self.cluster_state.nodes.values():
                if not node.ready:
                    failed_node = node.id
                    break

        step = runbook_next(
            ens_result.label, inc.service, inc.runbook_rung, failed_node
        )

        health_status = self.health_checker.status(inc.service)

        try:
            gate_result = trust_gate.evaluate(
                fv=fv,
                ensemble=ens_result,
                health_status=health_status,
                proposed_action=step.action,
                proposed_target=step.target,
                autonomy_stage=self.control.autonomy_stage,
                kill_switch=self.control.kill_switch,
                p_r1_threshold=self.control.p_r1_threshold,
                p_r2_threshold=self.control.p_r2_threshold,
            )
        except Exception as exc:
            logger.warning("Trust gate failed for %s: %s", inc.service, exc)
            return

        # Update incident with gate result
        inc.trust         = gate_result.trust
        inc.vetoes        = gate_result.vetoes
        inc.gate_decision = gate_result.decision
        inc.action_name   = step.action
        inc.action_target = step.target
        inc.action_risk   = gate_result.risk_class

        # Check ground truth correctness
        if inc.ground_truth_fault:
            from twin.faults import FAULT_TRUTH_LABEL, FaultKind
            try:
                truth_label = FAULT_TRUTH_LABEL[FaultKind(inc.ground_truth_fault)]
                inc.ground_truth_correct = (ens_result.label == truth_label)
            except Exception:
                pass

        self._queue_ws(WsMessage(
            type=WsMessageType.trust,
            sim_time=sim_time,
            incident_id=inc.id,
            payload={
                "trust": gate_result.trust.model_dump(),
                "vetoes": gate_result.vetoes,
                "decision": gate_result.decision.value,
                "reason": gate_result.reason,
                "action": step.action,
                "target": step.target,
                "risk_class": gate_result.risk_class,
            },
        ))

        self.audit.append(
            sim_time=sim_time,
            event_type="gate_decision",
            incident_id=inc.id,
            service=inc.service,
            action=step.action,
            target=step.target,
            risk_class=gate_result.risk_class,
            gate_decision=gate_result.decision.value,
            result=gate_result.decision.value,
            detail=gate_result.reason,
            autonomy_stage=self.control.autonomy_stage,
        )

        self._narrate(
            f"t={sim_time:.0f}s  {inc.service}: gate={gate_result.decision.value} "
            f"action={step.action} P_correct={gate_result.trust.P_correct:.2f}"
        )

        # ── Execute or Wait ───────────────────────────────────────────────────
        if gate_result.decision == GateDecision.ESCALATE:
            inc.approved = False
            inc.status = IncidentStatus.ESCALATED
            self._narrate(
                f"t={sim_time:.0f}s  {inc.service}: escalated -- {gate_result.reason}"
            )
            # Trigger triage for escalated incidents (never for AUTO — spec §10)
            self._schedule_triage(inc, gate_result)

        elif gate_result.decision == GateDecision.ASK:
            if approved_proposal == (step.action, step.target):
                inc.approved = False
                self._execute_action(inc, fv, step, gate_result, sim_time)
            else:
                if approved_proposal is not None:
                    inc.approved = False
                    self._narrate(
                        f"t={sim_time:.0f}s  {inc.service}: proposed action changed; "
                        "approval is required again"
                    )
                inc.status = IncidentStatus.WAITING
                self._narrate(
                    f"t={sim_time:.0f}s  {inc.service}: awaiting human approval "
                    f"for {step.action}"
                )
                # Trigger triage for ASK incidents
                self._schedule_triage(inc, gate_result)

        elif gate_result.decision == GateDecision.AUTO:
            self._execute_action(inc, fv, step, gate_result, sim_time)

    def _execute_action(self, inc: Incident, fv: Any, step: Any,
                        gate_result: Any, sim_time: float) -> None:
        self._pre_action_fv[inc.service] = fv
        inc.status = IncidentStatus.ACTING
        rec = self.executor.execute(
            action=step.action,
            target=step.target,
            service=inc.service,
            risk_class=gate_result.risk_class,
            sim_time=sim_time,
            incident_id=inc.id,
            gate_decision=gate_result.decision.value,
            autonomy_stage=self.control.autonomy_stage,
            attempt=inc.runbook_rung,
        )
        inc.actions.append(rec)

        self._queue_ws(WsMessage(
            type=WsMessageType.action,
            sim_time=sim_time,
            incident_id=inc.id,
            payload=rec.model_dump(),
        ))

        if rec.result_ok:
            inc.status = IncidentStatus.VERIFYING
            self._narrate(
                f"t={sim_time:.0f}s  {inc.service}: {step.action} executed -- "
                "verifying"
            )
        else:
            inc.status = IncidentStatus.ESCALATED
            self._narrate(
                f"t={sim_time:.0f}s  {inc.service}: {step.action} failed -- "
                f"{rec.result_detail}"
            )

    # ── Triage ─────────────────────────────────────────────────────────────────

    def _schedule_triage(self, inc: "Incident", gate_result: Any) -> None:
        """Schedule triage as a background asyncio task (non-blocking)."""
        asyncio.create_task(self._run_triage(inc, gate_result))

    async def _run_triage(self, inc: "Incident", gate_result: Any) -> None:
        """Run triage for ASK/ESCALATE and store the summary on the incident."""
        try:
            from triage.llm_triage import get_triage_provider
            provider = get_triage_provider()
            diag = inc.diagnosis.model_dump() if inc.diagnosis else {}
            trust = inc.trust.model_dump() if inc.trust else None
            summary = await provider.triage(
                incident_id=inc.id,
                service=inc.service,
                diagnosis=diag,
                trust=trust,
                gate_decision=gate_result.decision.value,
                vetoes=gate_result.vetoes,
                action_name=inc.action_name,
                sim_time=self.sim_time,
            )
            inc.triage_summary = summary.description
            self._queue_ws(WsMessage(
                type=WsMessageType.narration,
                sim_time=self.sim_time,
                incident_id=inc.id,
                payload={
                    "text": f"Triage ({inc.service}): {summary.description[:200]}",
                    "incident_id": inc.id,
                },
            ))
        except Exception as exc:
            logger.warning("Triage failed for %s: %s", inc.service, exc)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _narrate(self, text: str) -> None:
        self._narration.append(text)

    def _queue_ws(self, msg: WsMessage) -> None:
        self._pending_ws_messages.append(msg)

    async def _flush_ws(self) -> None:
        if not self._hub or not self._pending_ws_messages:
            self._pending_ws_messages.clear()
            return
        msgs = list(self._pending_ws_messages)
        self._pending_ws_messages.clear()
        for msg in msgs:
            await self._hub.broadcast(msg)
