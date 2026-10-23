"""
healing/executor.py - Guarded healing action executor.

The ONLY component allowed to change the simulated cluster.
Enforces (independently of the Trust Gate):
  1. Dry-run first (validate preconditions)
  2. Guardrail checks (allow-list, budget, cooldown)
  3. Audit log written BEFORE execution
  4. Execute via ClusterAdapter
  5. Record in audit log (result)

Does NOT undo on failure — that is the verifier's job (done after
the next telemetry tick).
"""

from __future__ import annotations

from adapters.base import ClusterAdapter
from healing.guardrails import GuardrailManager, GuardrailResult
from store.audit import AuditLog
from store.incidents import ActionRecord


class Executor:
    """
    Guarded executor. One instance per SimLoop.
    """

    def __init__(
        self,
        adapter: ClusterAdapter,
        guardrails: GuardrailManager,
        audit: AuditLog,
    ) -> None:
        self._adapter  = adapter
        self._guardrails = guardrails
        self._audit = audit

    def execute(
        self,
        action: str,
        target: str,
        service: str,
        risk_class: str,
        sim_time: float,
        incident_id: str,
        gate_decision: str,
        autonomy_stage: str,
        attempt: int = 0,
        params: dict | None = None,
    ) -> ActionRecord:
        """
        Execute one healing action.
        Returns an ActionRecord with the result.
        Audit log is written BEFORE execution.
        """
        params = params or {}
        snapshot = self._adapter.get_snapshot()

        # ── 1. Dry-run validation ────────────────────────────────────────────
        dry = self._adapter.dry_run(action, target, params)
        dry_ok = bool(dry.get("ok", False))
        dry_detail = str(dry.get("detail", ""))

        if not dry_ok:
            self._audit.append(
                sim_time=sim_time,
                event_type="action_vetoed",
                incident_id=incident_id,
                service=service,
                action=action,
                target=target,
                risk_class=risk_class,
                gate_decision=gate_decision,
                result="vetoed_dry_run",
                detail=f"Dry-run failed: {dry_detail}",
                autonomy_stage=autonomy_stage,
            )
            return ActionRecord(
                action=action, target=target, risk_class=risk_class,
                attempt=attempt, executed_at=sim_time,
                result_ok=False, result_detail=f"Dry-run failed: {dry_detail}",
                dry_run_ok=False, dry_run_detail=dry_detail,
            )

        # ── 2. Guardrail checks ───────────────────────────────────────────────
        guard: GuardrailResult = self._guardrails.check(
            action, service, sim_time, snapshot
        )
        if not guard.ok:
            self._audit.append(
                sim_time=sim_time,
                event_type="action_vetoed",
                incident_id=incident_id,
                service=service,
                action=action,
                target=target,
                risk_class=risk_class,
                gate_decision=gate_decision,
                result="vetoed_guardrail",
                detail=f"Guardrail: {guard.reason}",
                autonomy_stage=autonomy_stage,
            )
            return ActionRecord(
                action=action, target=target, risk_class=risk_class,
                attempt=attempt, executed_at=sim_time,
                result_ok=False, result_detail=f"Guardrail blocked: {guard.reason}",
                dry_run_ok=True, dry_run_detail=dry_detail,
            )

        # ── 3. Audit BEFORE execution ─────────────────────────────────────────
        self._audit.append(
            sim_time=sim_time,
            event_type="action_proposed",
            incident_id=incident_id,
            service=service,
            action=action,
            target=target,
            risk_class=risk_class,
            gate_decision=gate_decision,
            result="pending",
            detail=dry_detail,
            autonomy_stage=autonomy_stage,
            extra={"attempt": attempt, "guardrail_checks": guard.checks},
        )

        # ── 4. Execute ────────────────────────────────────────────────────────
        try:
            result = self._adapter.execute(action, target, params)
            ok     = bool(result.get("ok", False))
            detail = str(result.get("detail", ""))
        except Exception as exc:
            ok     = False
            detail = f"Executor exception: {exc}"

        # ── 5. Audit AFTER execution ──────────────────────────────────────────
        self._audit.append(
            sim_time=sim_time,
            event_type="action_executed",
            incident_id=incident_id,
            service=service,
            action=action,
            target=target,
            risk_class=risk_class,
            gate_decision=gate_decision,
            result="ok" if ok else "failed",
            detail=detail,
            autonomy_stage=autonomy_stage,
        )

        if ok:
            self._guardrails.record(service, sim_time)

        return ActionRecord(
            action=action, target=target, risk_class=risk_class,
            attempt=attempt, executed_at=sim_time,
            result_ok=ok, result_detail=detail,
            dry_run_ok=dry_ok, dry_run_detail=dry_detail,
        )

    def undo(
        self,
        action_record: ActionRecord,
        service: str,
        sim_time: float,
        incident_id: str,
        autonomy_stage: str,
    ) -> bool:
        """
        Attempt to undo a previously executed action.
        Only restart/scale_up/rollback/recreate are reversible.
        Returns True if undo was attempted.
        """
        undo_map = {
            "scale_up": "scale_down",  # not in adapter yet — handled as no_op
            "restart":  None,          # restart is self-reverting
            "rollback": None,          # rollback to v1 can't be undone without knowledge
            "recreate": None,          # recreate is self-reverting
        }
        action = action_record.action
        if action not in undo_map or not action_record.result_ok:
            return False

        # For demo purposes: scale_down is the only meaningful undo
        undo_action = undo_map.get(action)
        if undo_action is None:
            return False

        self._audit.append(
            sim_time=sim_time,
            event_type="action_undone",
            incident_id=incident_id,
            service=service,
            action=f"undo_{action}",
            target=action_record.target,
            risk_class=action_record.risk_class,
            gate_decision="AUTO",
            result="undo_attempted",
            detail=f"Undo of {action} ({action_record.result_detail})",
            autonomy_stage=autonomy_stage,
        )
        action_record.undo_executed = True
        return True
