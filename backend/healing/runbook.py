"""
healing/runbook.py - Runbook policy: diagnosis label -> action ladder.

Per master plan sec 8.6:
  CPU_OVERLOAD, TRAFFIC_OVERLOAD  -> scale_up (bounded), then escalate
  MEMORY_EXHAUSTION, MEMORY_LEAK  -> restart, then recreate, then escalate
  NETWORK_PARTITION               -> recreate, then reschedule, then escalate
  NODE_FAILURE                    -> reschedule, then recreate, then escalate
  BAD_DEPLOYMENT                  -> rollback, then escalate
  NORMAL                          -> no_op
  UNKNOWN                         -> escalate

Max 5 attempts per incident. Each call returns the next action given
the current runbook rung (attempt count).
"""

from __future__ import annotations

from dataclasses import dataclass

# Ladder: list of actions per diagnosis label, in order
# Last entry is always "escalate"
RUNBOOK: dict[str, list[str]] = {
    "CPU_OVERLOAD":       ["scale_up", "scale_up", "escalate"],
    "TRAFFIC_OVERLOAD":   ["scale_up", "scale_up", "escalate"],
    "MEMORY_EXHAUSTION":  ["restart", "recreate", "escalate"],
    "MEMORY_LEAK":        ["restart", "recreate", "escalate"],
    "NETWORK_PARTITION":  ["recreate", "reschedule", "escalate"],
    "NODE_FAILURE":       ["reschedule", "recreate", "escalate"],
    "BAD_DEPLOYMENT":     ["rollback", "escalate"],
    "NORMAL":             ["no_op"],
    "UNKNOWN":            ["escalate"],
}

MAX_ATTEMPTS = 5


@dataclass
class RunbookStep:
    action: str
    target: str
    is_last: bool   # True if next failure should escalate


def next_action(
    diagnosis_label: str,
    service_id: str,
    attempt: int,
    failed_node: str | None = None,
) -> RunbookStep:
    """
    Return the next runbook step for a given diagnosis and attempt count.

    attempt: 0-based index into the ladder (0 = first try).
    failed_node: for NODE_FAILURE, the target node to reschedule from.
    """
    ladder = RUNBOOK.get(diagnosis_label, ["escalate"])
    idx = min(attempt, len(ladder) - 1)
    action = ladder[idx]

    # Cap at MAX_ATTEMPTS
    if attempt >= MAX_ATTEMPTS:
        action = "escalate"

    # Determine target for the action
    if action == "reschedule":
        # Reschedule targets a node, not a service
        target = failed_node or service_id
    else:
        target = service_id

    is_last = (idx >= len(ladder) - 2) or (attempt >= MAX_ATTEMPTS - 1)

    return RunbookStep(action=action, target=target, is_last=is_last)
