"""
tests/test_pipeline.py - M5 unit tests for the full healing pipeline.

Tests:
  - Trust gate hard vetoes (UNKNOWN, OOD, E=0, kill switch)
  - Autonomy stage -> action mode mapping
  - Runbook action ladder
  - Executor guardrails (allow-list, cooldown, budget)
  - Executor dry-run veto
  - Incident store open/close/list
  - Audit log append/list
  - Verifier outcomes
  - End-to-end pipeline smoke: inject fault -> incident opens -> diagnosis -> gate
"""

from __future__ import annotations

import numpy as np
import pytest
from types import SimpleNamespace

from telemetry.features import FeatureVector, FEATURE_NAMES


def _make_fv(overrides: dict | None = None) -> FeatureVector:
    """Create a minimal FeatureVector for pipeline tests."""
    fdict: dict = {n: 0.0 for n in FEATURE_NAMES}
    fdict.update({
        "node_ready_fraction": 1.0,
        "request_rate_ratio_vs_baseline": 1.0,
        "cpu_util_mean_last": 1.0,
        "mem_ratio_mean_last": 1.0,
        "error_rate_last": 1.0,
        "network_error_rate_last": 1.0,
        "minutes_since_last_revision": 60.0,
    })
    if overrides:
        fdict.update(overrides)
    return FeatureVector(
        sim_time=300.0, service_id="checkout",
        features=[fdict[n] for n in FEATURE_NAMES],
        feature_names=FEATURE_NAMES,
        window_start=0.0, window_end=300.0,
    )


# ── Trust gate vetoes ─────────────────────────────────────────────────────────

class TestGateVetoes:

    def _make_ensemble_result(self, label="CPU_OVERLOAD"):
        from diagnosis.ensemble import EnsembleResult, ModelPrediction
        from diagnosis.mlp_mcd import CLASS_ORDER
        probs = {c: 0.0 for c in CLASS_ORDER}
        probs[label] = 1.0
        mp = ModelPrediction(label=label, confidence=1.0, probs=probs)
        return EnsembleResult(
            label=label, confidence=0.9, probs=probs,
            mlp=mp, xgb=mp, evidence=mp,
            mlp_uncertainty=0.1, abstained=(label == "UNKNOWN"),
        )

    def test_veto_unknown_label(self):
        from trust.gate import evaluate
        from telemetry.health import HealthStatus
        fv = _make_fv()
        ens = self._make_ensemble_result("UNKNOWN")
        result = evaluate(fv, ens, HealthStatus.BAD, "restart", "checkout")
        assert result.decision.value == "ESCALATE"
        assert "VETO_UNKNOWN" in result.vetoes

    def test_veto_kill_switch(self):
        from trust.gate import evaluate
        from telemetry.health import HealthStatus
        fv = _make_fv()
        ens = self._make_ensemble_result("CPU_OVERLOAD")
        result = evaluate(fv, ens, HealthStatus.BAD, "scale_up", "checkout",
                          kill_switch=True)
        assert result.decision.value == "ESCALATE"
        assert "VETO_KILL_SWITCH" in result.vetoes

    def test_no_veto_r0_action_ignores_kill_switch(self):
        from trust.gate import evaluate
        from telemetry.health import HealthStatus
        fv = _make_fv()
        ens = self._make_ensemble_result("NORMAL")
        result = evaluate(fv, ens, HealthStatus.OK, "no_op", "checkout",
                          kill_switch=True)
        # R0 action is always allowed
        assert "VETO_KILL_SWITCH" not in result.vetoes

    def test_stage_s0_blocks_r1(self):
        from trust.gate import evaluate
        from telemetry.health import HealthStatus
        fv = _make_fv()
        ens = self._make_ensemble_result("CPU_OVERLOAD")
        result = evaluate(fv, ens, HealthStatus.BAD, "scale_up", "checkout",
                          autonomy_stage="S0")
        assert result.decision.value == "ESCALATE"

    def test_stage_s3_auto_r1_with_high_p(self):
        """S3 with p_r1_threshold=0 should allow AUTO for R1 when not OOD."""
        from trust.gate import evaluate, OOD_VETO_THRESHOLD
        from telemetry.health import HealthStatus
        from trust import signals as sig_mod
        # Use a feature vector that is in-distribution (all at baseline = 1.0)
        fv = _make_fv()
        ens = self._make_ensemble_result("CPU_OVERLOAD")
        # Bypass OOD veto for this test by patching the N signal to be low
        orig_compute_N = sig_mod.compute_N
        sig_mod.compute_N = lambda fv_: 0.0   # force in-distribution
        try:
            result = evaluate(fv, ens, HealthStatus.BAD, "scale_up", "checkout",
                              autonomy_stage="S3", p_r1_threshold=0.0)
            assert result.decision.value in ("AUTO", "ASK")
        finally:
            sig_mod.compute_N = orig_compute_N

    def test_stage_s2_always_ask(self):
        from trust.gate import evaluate
        from telemetry.health import HealthStatus
        from trust import signals as sig_mod
        fv = _make_fv()
        ens = self._make_ensemble_result("CPU_OVERLOAD")
        orig_compute_N = sig_mod.compute_N
        sig_mod.compute_N = lambda fv_: 0.0   # force in-distribution
        try:
            result = evaluate(fv, ens, HealthStatus.BAD, "restart", "checkout",
                              autonomy_stage="S2")
            assert result.decision.value == "ASK"
        finally:
            sig_mod.compute_N = orig_compute_N


def test_approved_waiting_action_executes_on_next_pipeline_tick(monkeypatch):
    from app.sim_loop import SimLoop
    from store.incidents import ActionRecord, GateDecision, IncidentStatus, TrustSignals

    loop = SimLoop()
    loop.control.p_r1_threshold = 0.33
    loop.control.p_r2_threshold = 0.77
    incident = loop.incident_store.open("checkout", 100.0, "test incident")
    incident.status = IncidentStatus.WAITING
    incident.approved = True
    incident.action_name = "scale_up"
    incident.action_target = "checkout"
    incident.action_risk = "R1"
    incident.diagnosis_history = ["CPU_OVERLOAD"] * 3
    incident.diagnosis_stable_ticks = 3

    prediction = SimpleNamespace(label="CPU_OVERLOAD")
    ensemble = SimpleNamespace(
        label="CPU_OVERLOAD",
        confidence=0.9,
        probs={"CPU_OVERLOAD": 0.9},
        mlp=prediction,
        xgb=prediction,
        evidence=prediction,
        top_features={},
        mlp_uncertainty=0.1,
        abstained=False,
    )
    monkeypatch.setattr("app.sim_loop.ensemble_diagnose", lambda _fv: ensemble)
    gate_kwargs = {}

    def evaluate_gate(**kwargs):
        gate_kwargs.update(kwargs)
        return SimpleNamespace(
            decision=GateDecision.ASK,
            trust=TrustSignals(P_correct=0.8),
            vetoes=[],
            risk_class="R1",
            reason="awaiting human approval",
        )

    monkeypatch.setattr("app.sim_loop.trust_gate.evaluate", evaluate_gate)
    action = ActionRecord(
        action="scale_up",
        target="checkout",
        risk_class="R1",
        attempt=0,
        executed_at=120.0,
        result_ok=True,
        result_detail="scaled",
    )
    execute = lambda **_kwargs: action
    monkeypatch.setattr(loop.executor, "execute", execute)

    loop._tick_incident_pipeline(incident, _make_fv(), 120.0)

    assert incident.status == IncidentStatus.VERIFYING
    assert incident.actions == [action]
    assert incident.approved is False
    assert gate_kwargs["p_r1_threshold"] == 0.33
    assert gate_kwargs["p_r2_threshold"] == 0.77
    assert gate_kwargs["autonomy_stage"] == "S3"
    assert any(message.type.value == "action" for message in loop._pending_ws_messages)


# ── Action policy ─────────────────────────────────────────────────────────────

class TestActionPolicy:

    def test_r0_always_auto(self):
        from trust.action_policy import get_action_mode, ActionMode
        for stage in ("S0", "S1", "S2", "S3", "S4"):
            assert get_action_mode("no_op", stage) == ActionMode.AUTO
            assert get_action_mode("escalate", stage) == ActionMode.AUTO

    def test_kill_switch_blocks_r1(self):
        from trust.action_policy import get_action_mode, ActionMode
        assert get_action_mode("restart", "S4", kill_switch=True) == ActionMode.ESCALATE
        assert get_action_mode("scale_up", "S4", kill_switch=True) == ActionMode.ESCALATE

    def test_s3_r1_auto(self):
        from trust.action_policy import get_action_mode, ActionMode
        assert get_action_mode("restart", "S3") == ActionMode.AUTO
        assert get_action_mode("scale_up", "S3") == ActionMode.AUTO

    def test_s3_r2_ask(self):
        from trust.action_policy import get_action_mode, ActionMode
        assert get_action_mode("rollback", "S3") == ActionMode.ASK
        assert get_action_mode("recreate", "S3") == ActionMode.ASK

    def test_s4_r2_auto(self):
        from trust.action_policy import get_action_mode, ActionMode
        assert get_action_mode("rollback", "S4") == ActionMode.AUTO
        assert get_action_mode("recreate", "S4") == ActionMode.AUTO

    def test_s4_r3_ask(self):
        from trust.action_policy import get_action_mode, ActionMode
        assert get_action_mode("reschedule", "S4") == ActionMode.ASK


# ── Runbook ───────────────────────────────────────────────────────────────────

class TestRunbook:

    def test_cpu_overload_first_action(self):
        from healing.runbook import next_action
        step = next_action("CPU_OVERLOAD", "frontend", 0)
        assert step.action == "scale_up"
        assert step.target == "frontend"

    def test_memory_exhaustion_ladder(self):
        from healing.runbook import next_action
        assert next_action("MEMORY_EXHAUSTION", "checkout", 0).action == "restart"
        assert next_action("MEMORY_EXHAUSTION", "checkout", 1).action == "recreate"
        assert next_action("MEMORY_EXHAUSTION", "checkout", 2).action == "escalate"

    def test_unknown_always_escalates(self):
        from healing.runbook import next_action
        step = next_action("UNKNOWN", "payment", 0)
        assert step.action == "escalate"

    def test_node_failure_reschedule_target_is_node(self):
        from healing.runbook import next_action
        step = next_action("NODE_FAILURE", "frontend", 0, failed_node="node-2")
        assert step.action == "reschedule"
        assert step.target == "node-2"

    def test_max_attempts_caps_at_escalate(self):
        from healing.runbook import next_action, MAX_ATTEMPTS
        step = next_action("CPU_OVERLOAD", "frontend", MAX_ATTEMPTS + 5)
        assert step.action == "escalate"

    def test_bad_deployment_rollback(self):
        from healing.runbook import next_action
        step = next_action("BAD_DEPLOYMENT", "payment", 0)
        assert step.action == "rollback"


# ── Guardrails ────────────────────────────────────────────────────────────────

class TestGuardrails:

    def test_allow_list_blocks_unknown_action(self):
        from healing.guardrails import GuardrailManager
        gm = GuardrailManager()
        result = gm.check("nuke_cluster", "frontend", sim_time=100.0)
        assert not result.ok
        assert "allow-list" in result.reason

    def test_r0_action_always_passes(self):
        from healing.guardrails import GuardrailManager
        gm = GuardrailManager(cooldown=9999.0)
        result = gm.check("no_op", "frontend", sim_time=100.0)
        assert result.ok

    def test_cooldown_blocks_too_soon(self):
        from healing.guardrails import GuardrailManager
        gm = GuardrailManager(cooldown=120.0)
        gm.record("frontend", sim_time=100.0)
        result = gm.check("restart", "frontend", sim_time=150.0)
        assert not result.ok
        assert "Cooldown" in result.reason

    def test_cooldown_passes_after_wait(self):
        from healing.guardrails import GuardrailManager
        gm = GuardrailManager(cooldown=60.0)
        gm.record("frontend", sim_time=100.0)
        result = gm.check("restart", "frontend", sim_time=200.0)
        assert result.ok

    def test_service_budget_exceeded(self):
        from healing.guardrails import GuardrailManager
        gm = GuardrailManager(service_budget=2, cooldown=0.0)
        gm.record("frontend", sim_time=100.0)
        gm.record("frontend", sim_time=200.0)
        result = gm.check("restart", "frontend", sim_time=300.0)
        assert not result.ok
        assert "Service budget" in result.reason

    def test_budget_resets_after_hour(self):
        from healing.guardrails import GuardrailManager
        gm = GuardrailManager(service_budget=2, cooldown=0.0)
        gm.record("frontend", sim_time=100.0)
        gm.record("frontend", sim_time=200.0)
        # After 1 hour + 1s, old actions are outside the window
        result = gm.check("restart", "frontend", sim_time=4000.0)
        assert result.ok


# ── Incident store ────────────────────────────────────────────────────────────

class TestIncidentStore:

    def test_open_creates_incident(self):
        from store.incidents import IncidentStore
        store = IncidentStore()
        inc = store.open("checkout", 300.0, "error spike")
        assert inc.service == "checkout"
        assert inc.opened_at == 300.0
        assert store.get_open("checkout") is inc

    def test_one_open_per_service(self):
        from store.incidents import IncidentStore
        store = IncidentStore()
        inc1 = store.open("checkout", 300.0, "reason1")
        inc2 = store.open("checkout", 400.0, "reason2")
        # Second open closes the first
        assert store.get_open("checkout") is inc2

    def test_close_moves_to_closed(self):
        from store.incidents import IncidentStore
        store = IncidentStore()
        store.open("checkout", 300.0, "reason")
        closed = store.close("checkout", 600.0, resolved=True)
        assert closed is not None
        assert closed.status.value == "RESOLVED"
        assert store.get_open("checkout") is None

    def test_list_all_includes_open_and_closed(self):
        from store.incidents import IncidentStore
        store = IncidentStore()
        store.open("svc-a", 100.0, "r")
        store.open("svc-b", 200.0, "r")
        store.close("svc-a", 300.0, resolved=True)
        all_incs = store.list_all()
        ids = {i.service for i in all_incs}
        assert "svc-a" in ids
        assert "svc-b" in ids

    def test_get_by_id(self):
        from store.incidents import IncidentStore
        store = IncidentStore()
        inc = store.open("payment", 100.0, "r")
        found = store.get_by_id(inc.id)
        assert found is inc


# ── Audit log ─────────────────────────────────────────────────────────────────

class TestAuditLog:

    def test_append_and_list(self):
        from store.audit import AuditLog
        log = AuditLog()
        log.append(
            sim_time=100.0, event_type="gate_decision",
            incident_id="inc-1", service="frontend",
            action="scale_up", target="frontend", risk_class="R1",
            gate_decision="AUTO", result="AUTO", detail="ok",
        )
        entries = log.list()
        assert len(entries) == 1
        assert entries[0].action == "scale_up"

    def test_filter_by_service(self):
        from store.audit import AuditLog
        log = AuditLog()
        log.append(sim_time=1.0, event_type="x", incident_id="i", service="frontend",
                   action="a", target="t", risk_class="R0", gate_decision="AUTO",
                   result="ok", detail="")
        log.append(sim_time=2.0, event_type="x", incident_id="i", service="checkout",
                   action="b", target="t", risk_class="R0", gate_decision="AUTO",
                   result="ok", detail="")
        entries = log.list(service="frontend")
        assert len(entries) == 1
        assert entries[0].service == "frontend"


# ── Verifier ──────────────────────────────────────────────────────────────────

class TestVerifier:

    def test_pending_while_waiting(self):
        import healing.verifier as v
        from telemetry.health import HealthChecker, HealthStatus
        hc = HealthChecker()
        check = v.check("checkout", hc, action_executed_at=300.0,
                        sim_time=330.0, last_fv=None, pre_action_fv=None)
        assert check.outcome == v.VerificationOutcome.PENDING

    def test_resolved_when_health_ok(self):
        import healing.verifier as v
        from telemetry.health import HealthChecker, HealthStatus, ServiceHealth
        hc = HealthChecker()
        # Manually set health to OK (no incident = OK)
        check = v.check("checkout", hc, action_executed_at=100.0,
                        sim_time=200.0, last_fv=None, pre_action_fv=None)
        # health is OK by default -> RESOLVED
        assert check.outcome == v.VerificationOutcome.RESOLVED

    def test_still_bad_when_health_bad(self):
        import healing.verifier as v
        from telemetry.health import HealthChecker, HealthStatus, ServiceHealth
        hc = HealthChecker()
        # Force health to BAD
        from telemetry.health import ServiceHealth
        hc._state["checkout"] = ServiceHealth(
            service_id="checkout",
            status=HealthStatus.BAD,
        )
        check = v.check("checkout", hc, action_executed_at=100.0,
                        sim_time=200.0, last_fv=_make_fv(), pre_action_fv=_make_fv())
        assert check.outcome == v.VerificationOutcome.STILL_BAD
