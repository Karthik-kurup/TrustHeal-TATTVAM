"""
healing/verifier.py - Post-action verification.

After an action is executed, the verifier waits VERIFY_WAIT_TICKS telemetry
ticks and then re-evaluates the health check.

Outcomes:
  RESOLVED - health returned to OK and stayed OK through hysteresis
  STILL_BAD - health is still BAD -> try next runbook rung
  WORSE     - a new metric is worse than before -> undo and escalate

The verifier is called by the SimLoop on each telemetry tick when an
incident is in VERIFYING state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from telemetry.features import FeatureVector
from telemetry.health import HealthStatus, HealthChecker
from store.incidents import VerificationResult


# Wait this many telemetry ticks (× 15 sim-s) before checking
# 60 sim-s / 15 = 4 ticks, but spec says 60 sim-s initially
VERIFY_WAIT_SIM_SECONDS = 60.0   # sim-seconds to wait before checking


class VerificationOutcome(str, Enum):
    PENDING  = "PENDING"    # not enough time has passed yet
    RESOLVED = "RESOLVED"   # health is OK
    STILL_BAD = "STILL_BAD" # health still BAD -> try next rung
    WORSE    = "WORSE"      # metrics got worse -> undo + escalate


@dataclass
class VerificationCheck:
    outcome: VerificationOutcome
    health_status: str
    detail: str
    result: VerificationResult | None = None


def check(
    service_id: str,
    health_checker: HealthChecker,
    action_executed_at: float,
    sim_time: float,
    last_fv: FeatureVector | None,
    pre_action_fv: FeatureVector | None,
) -> VerificationCheck:
    """
    Check whether the service recovered after an action.

    action_executed_at: sim_time when the action was taken
    last_fv: most recent feature vector (after the action)
    pre_action_fv: feature vector just before the action (for worse comparison)
    """
    elapsed = sim_time - action_executed_at
    if elapsed < VERIFY_WAIT_SIM_SECONDS:
        return VerificationCheck(
            outcome=VerificationOutcome.PENDING,
            health_status="UNKNOWN",
            detail=f"Waiting: {elapsed:.0f}/{VERIFY_WAIT_SIM_SECONDS:.0f}s elapsed",
        )

    current_status = health_checker.status(service_id)
    metric_snapshot: dict[str, float] = {}

    if last_fv is not None:
        fd = last_fv.as_dict()
        metric_snapshot = {
            "cpu_util": round(fd.get("cpu_util_mean_last", 0.0), 3),
            "mem_ratio": round(fd.get("mem_ratio_mean_last", 0.0), 3),
            "error_rate": round(fd.get("error_rate_last", 0.0), 3),
            "latency_p95": round(fd.get("latency_p95_last", 0.0), 3),
        }

    if current_status == HealthStatus.OK:
        return VerificationCheck(
            outcome=VerificationOutcome.RESOLVED,
            health_status="OK",
            detail="Service health returned to OK",
            result=VerificationResult(
                checked_at=sim_time,
                health_status="OK",
                resolved=True,
                metric_snapshot=metric_snapshot,
            ),
        )

    # Health is still BAD — check if it got worse
    if last_fv is not None and pre_action_fv is not None:
        pre_fd  = pre_action_fv.as_dict()
        post_fd = last_fv.as_dict()
        # "Worse" = error rate or latency significantly higher than pre-action
        pre_err  = pre_fd.get("error_rate_last", 0.0)
        post_err = post_fd.get("error_rate_last", 0.0)
        pre_lat  = pre_fd.get("latency_p95_last", 0.0)
        post_lat = post_fd.get("latency_p95_last", 0.0)

        if (post_err > pre_err * 1.5 and post_err > 3.0) or \
           (post_lat > pre_lat * 2.0 and post_lat > 3.0):
            return VerificationCheck(
                outcome=VerificationOutcome.WORSE,
                health_status="WORSE",
                detail=f"Metrics degraded after action (err {pre_err:.1f}->{post_err:.1f})",
                result=VerificationResult(
                    checked_at=sim_time,
                    health_status="WORSE",
                    resolved=False,
                    metric_snapshot=metric_snapshot,
                ),
            )

    return VerificationCheck(
        outcome=VerificationOutcome.STILL_BAD,
        health_status="BAD",
        detail="Health still BAD after action — try next runbook rung",
        result=VerificationResult(
            checked_at=sim_time,
            health_status="BAD",
            resolved=False,
            metric_snapshot=metric_snapshot,
        ),
    )
