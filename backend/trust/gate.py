"""
trust/gate.py - The Trust Gate.

Combines trust signals into P_correct, applies hard vetoes, and returns
a gate decision: AUTO, ASK, or ESCALATE.

Hard vetoes (per master plan sec 8.5 - not learned):
  VETO_OOD     : N >= ood_threshold_normalised (i.e. N >= 1.0) -> ESCALATE
  VETO_E_ZERO  : E == 0.0 (evidence contradicts) for R1+ action -> ESCALATE
  VETO_UNKNOWN : diagnosis label is UNKNOWN -> ESCALATE
  VETO_LOW_CONF: P_correct < r1_threshold for R1, < r2_threshold for R2 -> ASK or ESCALATE

Thresholds (configurable via Control Center):
  p_correct_r1_threshold: default 0.70  (Wilson LCB proxy)
  p_correct_r2_threshold: default 0.85
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trust.signals import compute_all
from trust.calibration import compute_p_correct
from trust.action_policy import get_action_mode, get_risk_class, ActionMode
from diagnosis.ensemble import EnsembleResult
from telemetry.features import FeatureVector
from telemetry.health import HealthStatus
from store.incidents import GateDecision, TrustSignals


# Default P_correct thresholds (can be overridden by control state)
DEFAULT_P_R1 = 0.70   # R1 actions: require P_correct >= 0.70 to auto
DEFAULT_P_R2 = 0.85   # R2 actions: require P_correct >= 0.85 to auto

# OOD threshold: N >= this triggers veto (after normalisation N in [0,1])
OOD_VETO_THRESHOLD = 0.95   # 95% of training p99 distance


@dataclass
class GateResult:
    """Full output of the Trust Gate."""
    decision: GateDecision
    action: str               # proposed action from runbook
    target: str               # target service/node
    risk_class: str           # R0/R1/R2/R3
    trust: TrustSignals
    vetoes: list[str] = field(default_factory=list)
    reason: str = ""


def evaluate(
    fv: FeatureVector,
    ensemble: EnsembleResult,
    health_status: HealthStatus,
    proposed_action: str,
    proposed_target: str,
    autonomy_stage: str = "S3",
    kill_switch: bool = False,
    p_r1_threshold: float = DEFAULT_P_R1,
    p_r2_threshold: float = DEFAULT_P_R2,
) -> GateResult:
    """
    Run the Trust Gate for a proposed action.

    Returns a GateResult with decision AUTO / ASK / ESCALATE.
    """
    risk_class = get_risk_class(proposed_action)

    # ── Step 1: Compute trust signals ────────────────────────────────────────
    signals = compute_all(fv, ensemble, health_status)
    p_correct = compute_p_correct(signals)
    trust = TrustSignals(
        U=signals["U"],
        S=signals["S"],
        N=signals["N"],
        H=signals["H"],
        E=signals["E"],
        D=signals["D"],
        P_correct=p_correct,
    )

    vetoes: list[str] = []

    # ── Step 2: Hard vetoes (not learned) ────────────────────────────────────

    # VETO: UNKNOWN label always escalates
    if ensemble.label == "UNKNOWN":
        vetoes.append("VETO_UNKNOWN")

    # VETO: OOD (novelty above threshold) always escalates
    if signals["N"] >= OOD_VETO_THRESHOLD:
        vetoes.append("VETO_OOD")

    # VETO: Evidence contradiction (E=0) for R1+ actions
    if signals["E"] == 0.0 and risk_class in ("R1", "R2", "R3"):
        vetoes.append("VETO_E_ZERO")

    # VETO: Kill switch
    if kill_switch and risk_class != "R0":
        vetoes.append("VETO_KILL_SWITCH")

    if vetoes:
        return GateResult(
            decision=GateDecision.ESCALATE,
            action=proposed_action,
            target=proposed_target,
            risk_class=risk_class,
            trust=trust,
            vetoes=vetoes,
            reason=f"Hard veto(s): {', '.join(vetoes)}",
        )

    # ── Step 3: Autonomy stage default mode ──────────────────────────────────
    stage_mode: ActionMode = get_action_mode(proposed_action, autonomy_stage, kill_switch)

    if stage_mode == ActionMode.ESCALATE:
        # Stage blocks this action entirely
        return GateResult(
            decision=GateDecision.ESCALATE,
            action=proposed_action,
            target=proposed_target,
            risk_class=risk_class,
            trust=trust,
            reason=f"Stage {autonomy_stage} does not allow {risk_class} actions",
        )

    # ── Step 4: P_correct threshold check ────────────────────────────────────
    if stage_mode == ActionMode.AUTO:
        # Check confidence threshold for the risk class
        required_p = p_r1_threshold if risk_class == "R1" else p_r2_threshold
        if risk_class == "R0":
            required_p = 0.0   # R0 always passes

        if p_correct < required_p:
            # Downgrade to ASK
            stage_mode = ActionMode.ASK

    # ── Step 5: Map mode to decision ─────────────────────────────────────────
    if stage_mode == ActionMode.AUTO:
        decision = GateDecision.AUTO
        reason = f"P_correct={p_correct:.3f} >= {p_r1_threshold if risk_class == 'R1' else p_r2_threshold:.2f}"
    elif stage_mode == ActionMode.ASK:
        decision = GateDecision.ASK
        reason = f"P_correct={p_correct:.3f}; awaiting human approval"
    else:
        decision = GateDecision.ESCALATE
        reason = "Action blocked by autonomy stage"

    return GateResult(
        decision=decision,
        action=proposed_action,
        target=proposed_target,
        risk_class=risk_class,
        trust=trust,
        vetoes=vetoes,
        reason=reason,
    )
