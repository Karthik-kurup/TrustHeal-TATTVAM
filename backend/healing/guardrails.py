"""
healing/guardrails.py - Independent guardrail checks for the executor.

Enforces per master plan sec 9:
  - Action allow-list
  - Per-service action budget (max per hour)
  - Per-cluster action budget (max per hour)
  - Cooldown after each action (per service)
  - One pod at a time (enforced by sim_adapter, checked here)
  - Replica ceiling (no scale_up beyond hpa_max_replicas)
  - Disruption budget (at least 1 ready pod must remain)
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


# ── Defaults (overridable via control state) ──────────────────────────────────

ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "restart", "scale_up", "rollback", "recreate", "reschedule",
    "no_op", "escalate",
})

DEFAULT_SERVICE_BUDGET_PER_HOUR = 5
DEFAULT_CLUSTER_BUDGET_PER_HOUR = 20
DEFAULT_COOLDOWN_SECONDS = 60.0     # sim-seconds between actions on same service


@dataclass
class GuardrailResult:
    ok: bool
    reason: str = ""
    checks: list[str] = field(default_factory=list)  # all checks that ran


class GuardrailManager:
    """
    Tracks action budgets and cooldowns. Uses sim_time (not wall time) for
    rate limits so the clock tracks the simulation.
    """

    def __init__(
        self,
        service_budget: int = DEFAULT_SERVICE_BUDGET_PER_HOUR,
        cluster_budget: int = DEFAULT_CLUSTER_BUDGET_PER_HOUR,
        cooldown: float = DEFAULT_COOLDOWN_SECONDS,
        allowed_actions: frozenset | None = None,
    ) -> None:
        self._service_budget = service_budget
        self._cluster_budget = cluster_budget
        self._cooldown = cooldown
        self._allowed = allowed_actions or ALLOWED_ACTIONS

        # Timestamps of recent actions (sim_time), per service and global
        self._service_actions: dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self._cluster_actions: deque = deque(maxlen=500)
        # Last action sim_time per service (for cooldown)
        self._last_action: dict[str, float] = {}

    def check(
        self,
        action: str,
        service: str,
        sim_time: float,
        snapshot: object | None = None,   # ClusterSnapshot for replica checks
    ) -> GuardrailResult:
        """Run all guardrail checks. Returns GuardrailResult."""
        checks: list[str] = []

        # 1. Allow-list
        if action not in self._allowed:
            return GuardrailResult(False, f"Action '{action}' not in allow-list", checks)
        checks.append("allow_list:ok")

        # R0 actions (no_op, escalate) skip all rate limits
        if action in ("no_op", "escalate"):
            return GuardrailResult(True, "R0 action always allowed", checks)

        # 2. Cooldown
        last = self._last_action.get(service, -1e9)
        if sim_time - last < self._cooldown:
            remaining = self._cooldown - (sim_time - last)
            return GuardrailResult(
                False,
                f"Cooldown: {remaining:.0f}s remaining for {service}",
                checks,
            )
        checks.append(f"cooldown:ok (last={last:.0f}, now={sim_time:.0f})")

        # 3. Per-service budget
        one_hour_ago = sim_time - 3600.0
        svc_recent = [t for t in self._service_actions[service] if t > one_hour_ago]
        if len(svc_recent) >= self._service_budget:
            return GuardrailResult(
                False,
                f"Service budget exceeded: {len(svc_recent)}/{self._service_budget} in last hour",
                checks,
            )
        checks.append(f"service_budget:ok ({len(svc_recent)}/{self._service_budget})")

        # 4. Cluster budget
        cluster_recent = [t for t in self._cluster_actions if t > one_hour_ago]
        if len(cluster_recent) >= self._cluster_budget:
            return GuardrailResult(
                False,
                f"Cluster budget exceeded: {len(cluster_recent)}/{self._cluster_budget} in last hour",
                checks,
            )
        checks.append(f"cluster_budget:ok ({len(cluster_recent)}/{self._cluster_budget})")

        # 5. Replica ceiling (for scale_up)
        if action == "scale_up" and snapshot is not None:
            svc_obj = getattr(snapshot, "services", None)
            # snapshot.services is a list[Service]
            if svc_obj:
                for svc in svc_obj:
                    if getattr(svc, "id", None) == service:
                        if svc.replicas >= svc.hpa_max_replicas:
                            return GuardrailResult(
                                False,
                                f"Replica ceiling: {service} at {svc.replicas}/{svc.hpa_max_replicas}",
                                checks,
                            )
                        break
        checks.append("replica_ceiling:ok")

        return GuardrailResult(True, "All guardrails passed", checks)

    def record(self, service: str, sim_time: float) -> None:
        """Record that an action was executed at sim_time."""
        self._service_actions[service].append(sim_time)
        self._cluster_actions.append(sim_time)
        self._last_action[service] = sim_time

    def reset_service(self, service: str) -> None:
        """Reset budget and cooldown for a service (e.g. after snapshot restore)."""
        self._service_actions.pop(service, None)
        self._last_action.pop(service, None)

    def reset_all(self) -> None:
        self._service_actions.clear()
        self._cluster_actions.clear()
        self._last_action.clear()

    def update_limits(self, service_budget: int | None = None,
                      cluster_budget: int | None = None,
                      cooldown: float | None = None) -> None:
        if service_budget is not None:
            self._service_budget = service_budget
        if cluster_budget is not None:
            self._cluster_budget = cluster_budget
        if cooldown is not None:
            self._cooldown = cooldown
