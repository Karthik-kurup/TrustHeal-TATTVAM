"""
trust/action_policy.py - Map autonomy stage + action risk to AUTO/ASK/ESCALATE mode.

Autonomy stages (per master plan sec 8.7):
  S0  Shadow     - never act; recommend only (log decisions)
  S1  Recommend  - show recommendations; no auto or ask
  S2  Approve    - ASK for all non-R0 actions
  S3  Auto low-risk  - AUTO for R1; ASK for R2+
  S4  Auto medium-risk - AUTO for R1+R2; ASK for R3

Kill switch overrides everything -> S1 (recommend only).

Action risk classes:
  R0 - no_op, escalate  (always allowed)
  R1 - restart, scale_up
  R2 - recreate, rollback
  R3 - reschedule
"""

from __future__ import annotations

from enum import Enum


class AutonomyStage(str, Enum):
    S0 = "S0"   # Shadow - no actions
    S1 = "S1"   # Recommend - show only
    S2 = "S2"   # Approve - ask for everything
    S3 = "S3"   # Auto low-risk (R1 auto, R2+ ask)
    S4 = "S4"   # Auto medium-risk (R1+R2 auto, R3 ask)


class ActionMode(str, Enum):
    AUTO     = "AUTO"
    ASK      = "ASK"
    ESCALATE = "ESCALATE"   # action is blocked at this stage


# Risk class per action
ACTION_RISK: dict[str, str] = {
    "no_op":     "R0",
    "escalate":  "R0",
    "restart":   "R1",
    "scale_up":  "R1",
    "recreate":  "R2",
    "rollback":  "R2",
    "reschedule":"R3",
}

# Per-stage, per-risk-class default mode
# R0 always AUTO; others depend on stage
_STAGE_MATRIX: dict[str, dict[str, ActionMode]] = {
    "S0": {"R0": ActionMode.AUTO, "R1": ActionMode.ESCALATE, "R2": ActionMode.ESCALATE, "R3": ActionMode.ESCALATE},
    "S1": {"R0": ActionMode.AUTO, "R1": ActionMode.ESCALATE, "R2": ActionMode.ESCALATE, "R3": ActionMode.ESCALATE},
    "S2": {"R0": ActionMode.AUTO, "R1": ActionMode.ASK,      "R2": ActionMode.ASK,      "R3": ActionMode.ASK},
    "S3": {"R0": ActionMode.AUTO, "R1": ActionMode.AUTO,     "R2": ActionMode.ASK,      "R3": ActionMode.ASK},
    "S4": {"R0": ActionMode.AUTO, "R1": ActionMode.AUTO,     "R2": ActionMode.AUTO,     "R3": ActionMode.ASK},
}


def get_action_mode(action: str, stage: str, kill_switch: bool = False) -> ActionMode:
    """
    Return the ActionMode for an action given the current autonomy stage.
    Kill switch forces all non-R0 actions to ESCALATE (recommend only).
    """
    risk = ACTION_RISK.get(action, "R2")  # default R2 for unknown actions
    if risk == "R0":
        return ActionMode.AUTO

    if kill_switch:
        return ActionMode.ESCALATE

    matrix = _STAGE_MATRIX.get(stage, _STAGE_MATRIX["S3"])
    return matrix.get(risk, ActionMode.ASK)


def get_risk_class(action: str) -> str:
    """Return the risk class (R0/R1/R2/R3) for an action."""
    return ACTION_RISK.get(action, "R2")
