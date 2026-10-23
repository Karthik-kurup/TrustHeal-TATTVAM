"""
tests/test_api.py — M3 API contract tests.

Tests every REST endpoint for correct schema and status codes.
Uses TestClient (synchronous) — no real asyncio loop needed.
"""

from __future__ import annotations

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from fastapi.testclient import TestClient

from app.main import app

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    """Single TestClient for the whole module — starts/stops lifespan once."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ── Health ────────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert "sim_time" in d


# ── OpenAPI schema ────────────────────────────────────────────────────────────

def test_openapi_schema(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    paths = schema["paths"]
    # Verify every major endpoint is in the schema
    required_paths = [
        "/api/health",
        "/api/sim/state",
        "/api/sim/control",
        "/api/sim/snapshot",
        "/api/sim/restore",
        "/api/cluster",
        "/api/metrics/query",
        "/api/incidents",
        "/api/actions/manual",
        "/api/control/mode",
        "/api/control/killswitch",
        "/api/faults/inject",
        "/api/faults/active",
        "/api/scenarios",
        "/api/audit",
    ]
    for path in required_paths:
        assert path in paths, f"Missing path in OpenAPI schema: {path}"


# ── Simulation control ────────────────────────────────────────────────────────

def test_sim_state(client):
    r = client.get("/api/sim/state")
    assert r.status_code == 200
    d = r.json()
    assert "status" in d
    assert "sim_time" in d
    assert "speed" in d
    assert "seed" in d


def test_sim_pause_resume(client):
    r = client.post("/api/sim/control", json={"cmd": "pause"})
    assert r.status_code == 200
    assert r.json()["status"] == "paused"

    r = client.post("/api/sim/control", json={"cmd": "play"})
    assert r.status_code == 200
    assert r.json()["status"] == "running"


def test_sim_step(client):
    # Pause first
    client.post("/api/sim/control", json={"cmd": "pause"})
    before = client.get("/api/sim/state").json()["sim_time"]
    r = client.post("/api/sim/control", json={"cmd": "step"})
    assert r.status_code == 200
    after = client.get("/api/sim/state").json()["sim_time"]
    assert after >= before, "sim_time should advance after step"


def test_sim_speed(client):
    r = client.post("/api/sim/control", json={"cmd": "speed", "value": 5.0})
    assert r.status_code == 200
    state = client.get("/api/sim/state").json()
    assert state["speed"] == 5.0
    # Restore
    client.post("/api/sim/control", json={"cmd": "speed", "value": 10.0})


def test_sim_snapshot_restore(client):
    snap_r = client.post("/api/sim/snapshot")
    assert snap_r.status_code == 200
    snap_id = snap_r.json()["snapshot_id"]
    assert snap_id

    restore_r = client.post("/api/sim/restore", json={"snapshot_id": snap_id})
    assert restore_r.status_code == 200
    assert restore_r.json()["ok"] is True


def test_sim_restore_not_found(client):
    r = client.post("/api/sim/restore", json={"snapshot_id": "nonexistent"})
    assert r.status_code == 404


# ── Cluster ───────────────────────────────────────────────────────────────────

def test_cluster_state(client):
    r = client.get("/api/cluster")
    assert r.status_code == 200
    d = r.json()
    assert "nodes" in d
    assert "pods" in d
    assert "services" in d
    assert len(d["nodes"]) == 4        # 4 worker nodes
    assert len(d["services"]) == 6     # 6 shop services
    assert len(d["pods"]) >= 14        # at least 14 starting pods


# ── Metrics ───────────────────────────────────────────────────────────────────

def test_metrics_query_requires_service(client):
    r = client.get("/api/metrics/query")
    assert r.status_code == 422  # service is required


def test_metrics_query_empty_initially(client):
    r = client.get("/api/metrics/query?service=frontend&metric=cpu_util")
    assert r.status_code == 200
    # May be empty if no telemetry ticks yet
    assert isinstance(r.json(), list)


# ── Faults ────────────────────────────────────────────────────────────────────

def test_fault_inject_and_clear(client):
    r = client.post("/api/faults/inject", json={
        "kind": "cpu_stress",
        "target": "frontend",
        "intensity": 0.5,
        "onset_shape": "step",
        "onset_duration": 0,
        "duration": 60,
    })
    assert r.status_code == 200
    fault_id = r.json()["fault_id"]
    assert fault_id

    # Check active faults
    r2 = client.get("/api/faults/active")
    assert r2.status_code == 200
    ids = [f["id"] for f in r2.json()]
    assert fault_id in ids

    # Clear it
    r3 = client.delete(f"/api/faults/{fault_id}")
    assert r3.status_code == 200
    assert r3.json()["ok"] is True


def test_fault_inject_invalid_kind(client):
    r = client.post("/api/faults/inject", json={
        "kind": "not_a_real_fault",
        "target": "frontend",
    })
    assert r.status_code == 422


def test_fault_clear_not_found(client):
    r = client.delete("/api/faults/nonexistent-id")
    assert r.status_code == 404


# ── Actions ───────────────────────────────────────────────────────────────────

def test_manual_action_dry_run(client):
    r = client.post("/api/actions/manual", json={
        "action": "restart",
        "target": "frontend",
        "dry_run": True,
    })
    assert r.status_code == 200
    d = r.json()
    assert "ok" in d
    assert "detail" in d


def test_manual_action_execute(client):
    r = client.post("/api/actions/manual", json={
        "action": "scale_up",
        "target": "frontend",
        "dry_run": False,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ── Control ───────────────────────────────────────────────────────────────────

def test_get_control_mode(client):
    r = client.get("/api/control/mode")
    assert r.status_code == 200
    assert "autonomy_stage" in r.json()


def test_set_control_mode(client):
    r = client.put("/api/control/mode", json={"autonomy_stage": "S2"})
    assert r.status_code == 200
    assert r.json()["autonomy_stage"] == "S2"
    assert client.get("/api/control/state").json()["autonomy_stage"] == "S2"
    # Restore
    client.put("/api/control/mode", json={"autonomy_stage": "S3"})


def test_control_thresholds_are_applied_and_validated(client):
    original = client.get("/api/control/thresholds").json()
    response = client.put("/api/control/thresholds", json={"R1_auto": 0.65, "R2_auto": 0.9})
    assert response.status_code == 200
    assert response.json()["thresholds"]["R1_auto"] == 0.65
    assert response.json()["thresholds"]["R2_auto"] == 0.9
    assert client.get("/api/control/state").json()["thresholds"]["R1_auto"] == 0.65

    assert client.put("/api/control/thresholds", json={"R1_auto": 1.1}).status_code == 422
    assert client.put("/api/control/thresholds", json={"R1_auto": 0.95, "R2_auto": 0.8}).status_code == 422
    assert client.put("/api/control/thresholds", json={"R1_auto": None}).status_code == 422

    restored = client.put("/api/control/thresholds", json={
        "R1_auto": original["R1_auto"],
        "R2_auto": original["R2_auto"],
    })
    assert restored.status_code == 200


def test_control_limits_update_guardrails(client):
    sim = app.state.sim
    original = client.get("/api/control/limits").json()
    sim.guardrails.reset_all()
    response = client.put("/api/control/limits", json={
        "actions_per_service_per_hour": 1,
        "actions_per_cluster_per_hour": 2,
        "cooldown_seconds": 0,
    })
    assert response.status_code == 200
    assert response.json()["limits"]["actions_per_service_per_hour"] == 1

    assert sim.guardrails.check("restart", "frontend", sim.sim_time).ok
    sim.guardrails.record("frontend", sim.sim_time)
    blocked = sim.guardrails.check("restart", "frontend", sim.sim_time)
    assert not blocked.ok
    assert "Service budget exceeded" in blocked.reason

    assert client.put("/api/control/limits", json={"actions_per_service_per_hour": 0}).status_code == 422
    assert client.put("/api/control/limits", json={"actions_per_service_per_hour": 21}).status_code == 422
    assert client.put("/api/control/limits", json={"cooldown_seconds": -1}).status_code == 422
    assert client.put("/api/control/limits", json={}).status_code == 422
    restored = client.put("/api/control/limits", json={
        "actions_per_service_per_hour": original["actions_per_service_per_hour"],
        "actions_per_cluster_per_hour": original["actions_per_cluster_per_hour"],
        "cooldown_seconds": original["cooldown_seconds"],
    })
    assert restored.status_code == 200
    sim.guardrails.reset_all()


def test_kill_switch(client):
    r = client.post("/api/control/killswitch", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["kill_switch"] is True
    # Restore
    client.post("/api/control/killswitch", json={"enabled": False})


def test_incident_approval_only_accepts_pending_actions(client):
    from store.incidents import GateDecision, IncidentStatus

    sim = app.state.sim
    incident = sim.incident_store.open("approval-test", sim.sim_time, "test approval")
    incident.status = IncidentStatus.WAITING
    incident.action_name = "restart"
    incident.action_target = "approval-test"
    incident.action_risk = "R1"
    incident.gate_decision = GateDecision.ASK

    approved = client.post(f"/api/incidents/{incident.id}/approve", json={
        "action": "restart",
        "target": "approval-test",
    })
    assert approved.status_code == 200
    assert incident.approved is True
    changed = sim.incident_store.open("changed-approval-test", sim.sim_time, "changed action")
    changed.status = IncidentStatus.WAITING
    changed.gate_decision = GateDecision.ASK
    changed.action_name = "restart"
    changed.action_target = "changed-approval-test"
    assert client.post(f"/api/incidents/{changed.id}/approve", json={
        "action": "rollback",
        "target": "changed-approval-test",
    }).status_code == 409
    assert client.post(f"/api/incidents/{incident.id}/reject").status_code == 409

    rejected = sim.incident_store.open("rejection-test", sim.sim_time, "test rejection")
    rejected.status = IncidentStatus.WAITING
    rejected.gate_decision = GateDecision.ASK
    assert client.post(f"/api/incidents/{rejected.id}/reject").status_code == 200
    assert rejected.rejected is True
    assert client.post(f"/api/incidents/{rejected.id}/approve", json={
        "action": "restart",
        "target": "rejection-test",
    }).status_code == 409


def test_incident_proposal_returns_steps_and_current_mode(client, monkeypatch):
    from types import SimpleNamespace

    from diagnosis import ensemble
    from healing import runbook
    from store.incidents import GateDecision
    from trust import gate

    sim = app.state.sim
    incident = sim.incident_store.open(
        "proposal-test",
        sim.sim_time + 1,
        "test proposal",
    )
    feature_vector = SimpleNamespace()
    monkeypatch.setattr(sim.feature_engine, "compute", lambda *_args: feature_vector)
    monkeypatch.setattr(ensemble, "diagnose", lambda _fv: SimpleNamespace(
        label="CPU_OVERLOAD",
        confidence=0.9,
        abstained=False,
    ))
    monkeypatch.setattr(runbook, "next_action", lambda *_args: SimpleNamespace(
        action="scale_up",
        target="proposal-test",
    ))
    monkeypatch.setattr(sim.health_checker, "status", lambda _service: "BAD")
    monkeypatch.setattr(gate, "evaluate", lambda **_kwargs: SimpleNamespace(
        decision=GateDecision.ASK,
        risk_class="R1",
        trust=SimpleNamespace(P_correct=0.8),
        vetoes=[],
        reason="awaiting human approval",
    ))
    previous_stage = sim.control.autonomy_stage
    sim.control.autonomy_stage = "S2"

    try:
        response = client.get(f"/api/incidents/{incident.id}/proposal")
    finally:
        sim.control.autonomy_stage = previous_stage

    assert response.status_code == 200
    data = response.json()
    assert data["diagnosis"]["label"] == "CPU_OVERLOAD"
    assert data["proposal"]["action"] == "scale_up"
    assert data["proposal"]["decision"] == "ASK"
    assert len(data["proposal"]["steps"]) >= 3
    assert next(option for option in data["alternatives"] if option["action"] == "rollback")["mode"] == "ASK"


# ── Scenarios ─────────────────────────────────────────────────────────────────

def test_scenarios_list(client):
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    scenarios = r.json()
    assert isinstance(scenarios, list)
    ids = [s["id"] for s in scenarios]
    assert "shop_default" in ids
    assert "demo" in ids


# ── Incidents ─────────────────────────────────────────────────────────────────

def test_incidents_list(client):
    r = client.get("/api/incidents")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_incident_not_found(client):
    r = client.get("/api/incidents/nonexistent")
    assert r.status_code == 404


# ── Audit ─────────────────────────────────────────────────────────────────────

def test_audit_list(client):
    r = client.get("/api/audit")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── WebSocket reachability (just connect/disconnect) ──────────────────────────

def test_websocket_connects(client):
    with client.websocket_connect("/ws") as ws:
        # Just connecting and disconnecting should work
        pass


def test_models_report_matches_frontend_contract(client, tmp_path, monkeypatch):
    from app.api import models_report

    report = {
        "classification_report": {
            "CPU_OVERLOAD": {
                "precision": 0.8,
                "recall": 0.75,
                "f1-score": 0.77,
                "support": 12,
            },
            "weighted avg": {
                "precision": 0.8,
                "recall": 0.75,
                "f1-score": 0.77,
                "support": 12,
            },
            "macro avg": {
                "precision": 0.8,
                "recall": 0.75,
                "f1-score": 0.77,
                "support": 12,
            },
        },
        "unseen_faults": {"total": 0, "escalated": 0, "wrongly_auto": 0},
    }
    (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "iforest.joblib").touch()
    monkeypatch.setattr(models_report, "MODELS_DIR", tmp_path)

    response = client.get("/api/models/report")
    assert response.status_code == 200
    data = response.json()
    assert data["per_class"]["CPU_OVERLOAD"]["f1"] == 0.77
    assert data["weighted_avg"]["f1"] == 0.77
    assert data["unseen_fault_results"]["wrongly_auto_rate"] is None
    assert data["unseen_fault_results"]["wilson_ci_upper"] is None

    status = client.get("/api/models/status").json()
    assert status["artefacts"]["iforest"] is True


def test_compare_rejects_invalid_fault_schedule(client):
    response = client.post("/api/compare/run", json={
        "duration_sim_seconds": 1,
        "fault_schedule": [{"kind": "not_a_fault", "target": "frontend", "at": 0}],
    })
    assert response.status_code == 422


def test_compare_injects_scheduled_fault_at_simulation_time(monkeypatch):
    from app.api import compare
    from twin.faults import FaultRegistry

    injected: list[float] = []
    original_inject = FaultRegistry.inject

    def record_injection(self, spec, sim_time):
        injected.append(sim_time)
        return original_inject(self, spec, sim_time)

    monkeypatch.setattr(FaultRegistry, "inject", record_injection)
    result = compare._run_arm(
        "k8s_defaults",
        seed=42,
        duration=2,
        fault_schedule=[{
            "kind": "cpu_stress",
            "target": "frontend",
            "at": 0,
            "duration": 300,
            "intensity": 1.0,
        }],
    )

    assert result.mode == "k8s_defaults"
    assert injected == [0.0]


def test_compare_arm_is_deterministic_for_same_seed_and_schedule():
    from app.api import compare

    schedule = [{
        "kind": "cpu_stress",
        "target": "frontend",
        "at": 0,
        "duration": 300,
        "intensity": 1.0,
    }]
    first = compare._run_arm("k8s_defaults", 42, 30, schedule)
    second = compare._run_arm("k8s_defaults", 42, 30, schedule)

    assert first.metric_series == second.metric_series
