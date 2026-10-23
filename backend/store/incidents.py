"""
store/incidents.py - In-memory incident store with Pydantic models.

One open incident per service at any time. Incidents track the full pipeline:
  opened -> diagnosed -> decision -> action -> verification -> resolved/escalated

Persisted in-memory for the demo; a SQLite backend would be added for production.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class IncidentStatus(str, Enum):
    OPEN        = "OPEN"
    DIAGNOSED   = "DIAGNOSED"
    DECIDING    = "DECIDING"
    WAITING     = "WAITING"     # ASK: waiting for human approval
    ACTING      = "ACTING"
    VERIFYING   = "VERIFYING"
    RESOLVED    = "RESOLVED"
    ESCALATED   = "ESCALATED"


class GateDecision(str, Enum):
    AUTO      = "AUTO"
    ASK       = "ASK"
    ESCALATE  = "ESCALATE"


# ── Sub-models ────────────────────────────────────────────────────────────────

class TrustSignals(BaseModel):
    U: float = 0.5   # MC-dropout uncertainty (0=certain, 1=very uncertain)
    S: float = 0.5   # Attribution stability
    N: float = 0.5   # Novelty (OOD Mahalanobis, normalised 0=in-dist, 1=far OOD)
    H: float = 0.5   # Health agreement (1 if health still BAD + fault class)
    E: float = 0.5   # Evidence agreement (1=confirm, 0.5=silent, 0=contradict)
    D: float = 0.5   # Diagnoser agreement (1=MLP==XGB, 0=disagree)
    P_correct: float = 0.5   # Calibrated gate probability


class DiagnosisResult(BaseModel):
    label: str
    confidence: float
    probs: dict[str, float] = Field(default_factory=dict)
    mlp_label: str = ""
    xgb_label: str = ""
    evidence_label: str = ""
    top_features: dict[str, float] = Field(default_factory=dict)
    mlp_uncertainty: float = 0.0
    abstained: bool = False


class ActionRecord(BaseModel):
    action: str
    target: str
    risk_class: str
    attempt: int
    executed_at: float
    result_ok: bool
    result_detail: str
    dry_run_ok: bool = True
    dry_run_detail: str = ""
    undo_executed: bool = False


class VerificationResult(BaseModel):
    checked_at: float
    health_status: str    # OK / BAD / WORSE
    resolved: bool
    metric_snapshot: dict[str, float] = Field(default_factory=dict)


# ── Main Incident model ───────────────────────────────────────────────────────

class Incident(BaseModel):
    id: str
    service: str
    opened_at: float
    closed_at: float | None = None
    status: IncidentStatus = IncidentStatus.OPEN
    health_reason: str = ""

    # Diagnosis
    diagnosis: DiagnosisResult | None = None
    diagnosis_stable_ticks: int = 0  # how many ticks the same label has held
    diagnosis_history: list[str] = Field(default_factory=list)  # last N labels

    # Trust
    trust: TrustSignals | None = None
    vetoes: list[str] = Field(default_factory=list)  # names of hard vetoes triggered

    # Decision
    gate_decision: GateDecision | None = None
    action_name: str = ""      # the chosen action
    action_target: str = ""
    action_risk: str = ""      # R0/R1/R2/R3
    runbook_rung: int = 0      # which rung of the ladder we are on

    # Human control
    approved: bool = False
    rejected: bool = False
    approval_time: float | None = None
    rejection_time: float | None = None

    # Actions
    actions: list[ActionRecord] = Field(default_factory=list)

    # Verification
    verification: VerificationResult | None = None

    # Timeline (for replay)
    event_timeline: list[dict[str, Any]] = Field(default_factory=list)

    # Triage
    triage_summary: str = ""

    # Ground truth (injected fault, for demo reveal)
    ground_truth_fault: str | None = None
    ground_truth_correct: bool | None = None  # was the diagnosis right?

    # MTTR
    time_to_recover: float | None = None

    def add_event(self, kind: str, payload: dict) -> None:
        self.event_timeline.append({
            "kind": kind,
            "sim_time": payload.get("sim_time", 0.0),
            "payload": payload,
        })

    def close(self, sim_time: float, resolved: bool) -> None:
        self.closed_at = sim_time
        self.status = IncidentStatus.RESOLVED if resolved else IncidentStatus.ESCALATED
        if self.opened_at:
            self.time_to_recover = sim_time - self.opened_at


# ── Store ─────────────────────────────────────────────────────────────────────

class IncidentStore:
    """
    In-memory incident store.
    One open incident per service; closed incidents archived up to MAX_CLOSED.
    """
    MAX_CLOSED = 200

    def __init__(self) -> None:
        self._open: dict[str, Incident] = {}       # service_id -> incident
        self._closed: list[Incident] = []

    def open(self, service_id: str, sim_time: float,
             health_reason: str) -> Incident:
        """Open a new incident. Closes any existing open one first (shouldn't happen)."""
        if service_id in self._open:
            self.close(service_id, sim_time, resolved=False)
        inc = Incident(
            id=f"inc-{service_id}-{int(sim_time)}",
            service=service_id,
            opened_at=sim_time,
            health_reason=health_reason,
        )
        self._open[service_id] = inc
        return inc

    def get_open(self, service_id: str) -> Incident | None:
        return self._open.get(service_id)

    def get_by_id(self, incident_id: str) -> Incident | None:
        for inc in self._open.values():
            if inc.id == incident_id:
                return inc
        for inc in self._closed:
            if inc.id == incident_id:
                return inc
        return None

    def close(self, service_id: str, sim_time: float, resolved: bool) -> Incident | None:
        inc = self._open.pop(service_id, None)
        if inc:
            inc.close(sim_time, resolved)
            self._closed.append(inc)
            if len(self._closed) > self.MAX_CLOSED:
                self._closed.pop(0)
        return inc

    def list_open(self) -> list[Incident]:
        return list(self._open.values())

    def list_all(self, limit: int = 50) -> list[Incident]:
        all_incs = list(self._open.values()) + list(reversed(self._closed))
        return all_incs[:limit]

    def clear(self) -> None:
        self._open.clear()
        self._closed.clear()
