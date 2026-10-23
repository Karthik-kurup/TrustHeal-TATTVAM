"""app/api/incidents.py - Incident list, detail, approve, reject endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from store.incidents import IncidentStatus

router = APIRouter()


class ApprovalRequest(BaseModel):
    action: str = Field(min_length=1)
    target: str = Field(min_length=1)


@router.get("/incidents")
async def list_incidents(request: Request):
    sim = request.app.state.sim
    return [inc.model_dump() for inc in sim.incident_store.list_all(limit=50)]


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str, request: Request):
    sim = request.app.state.sim
    inc = sim.incident_store.get_by_id(incident_id)
    if inc is None:
        raise HTTPException(404, f"Incident {incident_id!r} not found")
    return inc.model_dump()


@router.get("/incidents/{incident_id}/proposal")
async def get_incident_proposal(incident_id: str, request: Request):
    """Return a fresh, non-executing proposal for the incident's current diagnosis."""
    from diagnosis.ensemble import diagnose
    from healing.runbook import next_action
    from trust.action_policy import get_action_mode
    from trust.gate import evaluate

    sim = request.app.state.sim
    incident = sim.incident_store.get_by_id(incident_id)
    if incident is None:
        raise HTTPException(404, f"Incident {incident_id!r} not found")
    if incident.status == IncidentStatus.RESOLVED:
        raise HTTPException(409, "A proposal is not available for a resolved incident")

    fv = sim.feature_engine.compute(incident.service, sim.telemetry)
    if fv is None:
        raise HTTPException(409, "Not enough telemetry is available to propose an action yet")

    ensemble = diagnose(fv)
    failed_node = None
    if ensemble.label == "NODE_FAILURE":
        failed_node = next(
            (node.id for node in sim.cluster_state.nodes.values() if not node.ready),
            None,
        )
    step = next_action(ensemble.label, incident.service, incident.runbook_rung, failed_node)
    health_status = sim.health_checker.status(incident.service)
    gate_result = evaluate(
        fv=fv,
        ensemble=ensemble,
        health_status=health_status,
        proposed_action=step.action,
        proposed_target=step.target,
        autonomy_stage=sim.control.autonomy_stage,
        kill_switch=sim.control.kill_switch,
        p_r1_threshold=sim.control.p_r1_threshold,
        p_r2_threshold=sim.control.p_r2_threshold,
    )
    action_steps = {
        "restart": "Restart the service pods, subject to cooldown and action budgets",
        "scale_up": "Add one replica, subject to the replica ceiling and action budgets",
        "recreate": "Recreate an unhealthy pod, subject to disruption guardrails",
        "rollback": "Roll back the latest deployment, subject to the action allow-list",
        "reschedule": f"Reschedule workloads away from node {step.target}",
        "no_op": "Take no action because the current diagnosis is healthy",
        "escalate": "Do not modify the cluster; escalate for operator investigation",
    }

    alternatives = []
    from trust.action_policy import get_risk_class

    for action, description in (
        ("restart", "Restart the service pods"),
        ("scale_up", "Add one service replica"),
        ("recreate", "Recreate an unhealthy pod"),
        ("rollback", "Roll back the latest deployment"),
        ("reschedule", "Move workloads away from an unhealthy node"),
    ):
        if action == step.action:
            continue
        risk_class = get_risk_class(action)
        alternatives.append({
            "action": action,
            "description": description,
            "risk_class": risk_class,
            "mode": get_action_mode(action, sim.control.autonomy_stage, sim.control.kill_switch).value,
            "selected_by_runbook": False,
        })

    return {
        "incident_id": incident.id,
        "service": incident.service,
        "diagnosis": {
            "label": ensemble.label,
            "confidence": ensemble.confidence,
            "abstained": ensemble.abstained,
        },
        "proposal": {
            "action": step.action,
            "target": step.target,
            "risk_class": gate_result.risk_class,
            "decision": gate_result.decision.value,
            "p_correct": gate_result.trust.P_correct,
            "reason": gate_result.reason,
            "vetoes": gate_result.vetoes,
            "steps": [
                f"Diagnose current telemetry: {ensemble.label} ({ensemble.confidence:.0%} confidence)",
                action_steps.get(step.action, f"Apply {step.action} to {step.target}"),
                "Check the action allow-list, cooldown, budgets, and disruption limits",
                "Apply the trust gate, autonomy stage, confidence thresholds, and safety vetoes",
                "Execute only if permitted; otherwise wait for approval or escalate",
                "Verify service health and record the outcome in the incident audit trail",
            ],
        },
        "alternatives": alternatives,
        "stage": sim.control.autonomy_stage,
        "kill_switch": sim.control.kill_switch,
        "generated_at": sim.sim_time,
    }


@router.post("/incidents/{incident_id}/approve")
async def approve_incident(incident_id: str, body: ApprovalRequest, request: Request):
    """Approve a pending ASK action for this incident."""
    sim = request.app.state.sim
    inc = sim.incident_store.get_by_id(incident_id)
    if inc is None:
        raise HTTPException(404, f"Incident {incident_id!r} not found")
    if inc.status != IncidentStatus.WAITING or inc.rejected or inc.gate_decision is None:
        raise HTTPException(409, "Incident is not awaiting approval")
    if inc.gate_decision.value != "ASK":
        raise HTTPException(409, "Incident no longer has an approval-required decision")
    if (body.action, body.target) != (inc.action_name, inc.action_target):
        raise HTTPException(409, "The proposed action changed; review the current response plan")
    sim.audit.append(
        sim_time=sim.sim_time,
        event_type="human_approval",
        incident_id=inc.id,
        service=inc.service,
        action=inc.action_name,
        target=inc.action_target,
        risk_class=inc.action_risk,
        gate_decision="ASK",
        result="approved",
        detail="Human approved the proposed action",
        autonomy_stage=sim.control.autonomy_stage,
    )
    inc.approved = True
    inc.approval_time = sim.sim_time
    return {"ok": True, "incident_id": incident_id}


@router.post("/incidents/{incident_id}/reject")
async def reject_incident(incident_id: str, request: Request):
    """Reject a pending ASK action - escalate instead."""
    sim = request.app.state.sim
    inc = sim.incident_store.get_by_id(incident_id)
    if inc is None:
        raise HTTPException(404, f"Incident {incident_id!r} not found")
    if inc.status != IncidentStatus.WAITING or inc.approved:
        raise HTTPException(409, "Incident is not awaiting rejection")
    sim.audit.append(
        sim_time=sim.sim_time,
        event_type="human_rejection",
        incident_id=inc.id,
        service=inc.service,
        action=inc.action_name,
        target=inc.action_target,
        risk_class=inc.action_risk,
        gate_decision="ASK",
        result="rejected",
        detail="Human rejected the proposed action",
        autonomy_stage=sim.control.autonomy_stage,
    )
    inc.rejected = True
    inc.rejection_time = sim.sim_time
    return {"ok": True, "incident_id": incident_id}
