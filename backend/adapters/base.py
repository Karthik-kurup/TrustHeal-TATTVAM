"""
adapters/base.py — ClusterAdapter abstract base class.

The pipeline ONLY talks to this interface, never to twin internals directly.
This allows the same pipeline to run against the digital twin (SimAdapter)
or a real cluster (KubeAdapter — COULD, read-only, disabled by default).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from twin.cluster import ClusterSnapshot


class ClusterAdapter(ABC):
    """
    Abstract interface between the healing pipeline and the cluster backend.
    All methods are synchronous; async wrappers live in the SimLoop.
    """

    @abstractmethod
    def get_snapshot(self) -> ClusterSnapshot:
        """Return current cluster state (read-only snapshot)."""
        ...

    @abstractmethod
    def get_events(self) -> list[dict]:
        """
        Return and drain pending cluster events since last call.
        Each event is a dict compatible with ClusterEvent.model_dump().
        """
        ...

    @abstractmethod
    def execute(self, action: str, target: str, params: dict[str, Any]) -> dict:
        """
        Execute a healing action against the cluster.
        Returns a result dict: {"ok": bool, "detail": str, ...}
        KubeAdapter MUST raise NotImplementedError — it is read-only.
        """
        ...

    @abstractmethod
    def dry_run(self, action: str, target: str, params: dict[str, Any]) -> dict:
        """
        Validate an action without executing it.
        Returns {"ok": bool, "detail": str, "affected_pods": list[str]}.
        """
        ...
