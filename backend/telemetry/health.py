"""
telemetry/health.py — Health check with hysteresis.

Rules (per master plan §7):
  BAD after:  2 consecutive bad samples  OR  1 severe sample
  OK after:   4 consecutive good samples (initial 8 — becomes 4 after first incident)
  OOM and restarts count as unhealthy.
  One open incident per service.
  A stale window cannot trigger action while health is OK.

HealthChecker is stateless except for the hysteresis counters; the IncidentStore
is separate (M3+). Here we just emit HealthChange events that the SimLoop
observes to open/close incidents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from telemetry.features import FeatureVector


# ── Thresholds ────────────────────────────────────────────────────────────────

# A sample is BAD if any of these normalised feature ratios exceed threshold
BAD_THRESHOLDS: dict[str, float] = {
    "error_rate_last":          5.0,   # 5× baseline
    "cpu_util_mean_last":       1.8,   # 1.8× baseline
    "mem_ratio_mean_last":      1.6,   # 1.6× baseline
    "latency_p95_last":         3.0,   # 3× baseline
    "restart_delta_last_tick":  0.5,   # any restart in last tick (>0 → bad)
    "unknown_pods_count_normalised": 0.01,  # any Unknown pod
}

# A sample is SEVERE if any of these exceed threshold (triggers BAD on 1 sample)
SEVERE_THRESHOLDS: dict[str, float] = {
    "error_rate_last":          10.0,  # 10× baseline
    "last_termination_reason_oom": 0.5,  # OOMKilled
    "mem_ratio_mean_last":      1.95,  # near OOM territory
    "node_ready_fraction":      0.75,  # <75% of nodes ready (invert: < threshold = bad)
}

# Hysteresis counters
BAD_AFTER_CONSECUTIVE = 2        # bad samples to open incident
SEVERE_OPENS_IMMEDIATELY = True  # 1 severe sample = instant BAD
OK_AFTER_CONSECUTIVE_INITIAL = 8  # first incident: 8 good samples to close
OK_AFTER_CONSECUTIVE = 4         # subsequent: 4 good samples


class HealthStatus(str, Enum):
    OK = "OK"
    BAD = "BAD"


@dataclass
class ServiceHealth:
    """Hysteresis state for one service."""
    service_id: str
    status: HealthStatus = HealthStatus.OK
    consecutive_bad: int = 0
    consecutive_good: int = 0
    incident_count: int = 0      # total incidents opened so far
    ok_threshold: int = OK_AFTER_CONSECUTIVE_INITIAL  # adjusts after first incident

    def ok_threshold_current(self) -> int:
        return OK_AFTER_CONSECUTIVE_INITIAL if self.incident_count == 0 \
               else OK_AFTER_CONSECUTIVE


@dataclass
class HealthEvent:
    """Emitted by HealthChecker when a service transitions health state."""
    service_id: str
    sim_time: float
    old_status: HealthStatus
    new_status: HealthStatus
    reason: str                  # human-readable
    fv: FeatureVector            # feature vector that triggered the change


class HealthChecker:
    """
    Stateful health checker with hysteresis per service.
    Called every telemetry tick (15 sim-s) by the SimLoop.
    Returns a list of HealthEvent objects (may be empty).
    """

    def __init__(self) -> None:
        self._state: dict[str, ServiceHealth] = {}

    def evaluate(self, fv: FeatureVector) -> list[HealthEvent]:
        """
        Evaluate a feature vector and return any health state change events.
        """
        svc_id = fv.service_id
        if svc_id not in self._state:
            self._state[svc_id] = ServiceHealth(service_id=svc_id)

        state = self._state[svc_id]
        old_status = state.status
        events: list[HealthEvent] = []

        is_bad, is_severe, reason = _classify_sample(fv)

        if is_severe and SEVERE_OPENS_IMMEDIATELY:
            state.consecutive_bad = BAD_AFTER_CONSECUTIVE
            state.consecutive_good = 0
        elif is_bad:
            state.consecutive_bad += 1
            state.consecutive_good = 0
        else:
            state.consecutive_good += 1
            state.consecutive_bad = 0

        # Transition: OK → BAD
        if (state.status == HealthStatus.OK
                and state.consecutive_bad >= BAD_AFTER_CONSECUTIVE):
            state.status = HealthStatus.BAD
            state.incident_count += 1
            events.append(HealthEvent(
                service_id=svc_id,
                sim_time=fv.sim_time,
                old_status=old_status,
                new_status=HealthStatus.BAD,
                reason=reason,
                fv=fv,
            ))

        # Transition: BAD → OK
        elif (state.status == HealthStatus.BAD
              and state.consecutive_good >= state.ok_threshold_current()):
            state.status = HealthStatus.OK
            state.consecutive_bad = 0
            events.append(HealthEvent(
                service_id=svc_id,
                sim_time=fv.sim_time,
                old_status=old_status,
                new_status=HealthStatus.OK,
                reason="Recovered: consecutive good samples",
                fv=fv,
            ))

        return events

    def status(self, service_id: str) -> HealthStatus:
        return self._state.get(
            service_id, ServiceHealth(service_id=service_id)
        ).status

    def state(self, service_id: str) -> ServiceHealth:
        return self._state.get(
            service_id, ServiceHealth(service_id=service_id)
        )

    def reset(self, service_id: str) -> None:
        """Reset health state for a service (e.g. after snapshot restore)."""
        self._state.pop(service_id, None)

    def reset_all(self) -> None:
        self._state.clear()


def _classify_sample(fv: FeatureVector) -> tuple[bool, bool, str]:
    """
    Returns (is_bad, is_severe, reason_string).
    Checks normalised feature values against BAD_THRESHOLDS and SEVERE_THRESHOLDS.
    """
    fd = fv.as_dict()
    reason_parts: list[str] = []

    # Check severe first
    for feat, threshold in SEVERE_THRESHOLDS.items():
        val = fd.get(feat, 0.0)
        # node_ready_fraction: BAD if < threshold (inverse logic)
        if feat == "node_ready_fraction":
            if val < threshold:
                return True, True, f"SEVERE: {feat}={val:.3f} < {threshold}"
        else:
            if val >= threshold:
                return True, True, f"SEVERE: {feat}={val:.3f} >= {threshold}"

    # Check bad
    for feat, threshold in BAD_THRESHOLDS.items():
        val = fd.get(feat, 0.0)
        if val >= threshold:
            reason_parts.append(f"{feat}={val:.3f}")

    if reason_parts:
        return True, False, "BAD: " + ", ".join(reason_parts)

    return False, False, ""
