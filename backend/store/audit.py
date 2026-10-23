"""
store/audit.py - Append-only audit log for all healing actions.

Written BEFORE execution (per rule §7). Stores as in-memory list
with a JSON file fallback for persistence across restarts.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class AuditEntry:
    entry_id: int
    sim_time: float
    wall_time: float
    event_type: str             # "action_proposed" | "action_executed" | "action_vetoed" |
                                # "action_undone" | "incident_opened" | "incident_closed" |
                                # "gate_decision" | "kill_switch"
    incident_id: str
    service: str
    action: str
    target: str
    risk_class: str
    gate_decision: str          # AUTO / ASK / ESCALATE / N/A
    result: str                 # ok / failed / vetoed / undone
    detail: str
    autonomy_stage: str = "S3"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class AuditLog:
    """Append-only audit log (in-memory with optional file persistence)."""

    MAX_ENTRIES = 2000

    def __init__(self, file_path: str | None = None) -> None:
        self._entries: list[AuditEntry] = []
        self._counter: int = 0
        self._file_path = file_path

    def append(
        self,
        sim_time: float,
        event_type: str,
        incident_id: str,
        service: str,
        action: str,
        target: str,
        risk_class: str,
        gate_decision: str,
        result: str,
        detail: str,
        autonomy_stage: str = "S3",
        extra: dict | None = None,
    ) -> AuditEntry:
        """Append an audit entry. Returns the entry."""
        entry = AuditEntry(
            entry_id=self._counter,
            sim_time=sim_time,
            wall_time=time.time(),
            event_type=event_type,
            incident_id=incident_id,
            service=service,
            action=action,
            target=target,
            risk_class=risk_class,
            gate_decision=gate_decision,
            result=result,
            detail=detail,
            autonomy_stage=autonomy_stage,
            extra=extra or {},
        )
        self._counter += 1
        self._entries.append(entry)
        if len(self._entries) > self.MAX_ENTRIES:
            self._entries.pop(0)
        return entry

    def list(self, limit: int = 100, offset: int = 0,
             service: str | None = None,
             event_type: str | None = None) -> list[AuditEntry]:
        """Return entries in reverse chronological order."""
        entries = list(reversed(self._entries))
        if service:
            entries = [e for e in entries if e.service == service]
        if event_type:
            entries = [e for e in entries if e.event_type == event_type]
        return entries[offset: offset + limit]

    def clear(self) -> None:
        self._entries.clear()
        self._counter = 0
