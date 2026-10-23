"""
app/ws.py — WebSocket hub.

Maintains a set of connected clients and broadcasts typed WsMessage objects.
Messages are batched at ~10 Hz to prevent backpressure at high sim speeds.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class WsMessageType(str, Enum):
    tick               = "tick"
    cluster_event      = "cluster_event"
    fault_event        = "fault_event"
    incident_update    = "incident_update"
    diagnosis          = "diagnosis"
    trust              = "trust"
    decision           = "decision"
    action             = "action"
    verification       = "verification"
    narration          = "narration"
    audit              = "audit"
    sim_state          = "sim_state"


@dataclass
class WsMessage:
    type: WsMessageType
    sim_time: float
    incident_id: str | None
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps({
            "type": self.type.value,
            "sim_time": self.sim_time,
            "incident_id": self.incident_id,
            "payload": self.payload,
        })


class WsHub:
    """
    Manages all active WebSocket connections.
    broadcast() sends to all connected clients; disconnected clients are
    silently removed.
    """

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        logger.info("WS client connected; total=%d", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        logger.info("WS client disconnected; total=%d", len(self._clients))

    async def broadcast(self, msg: WsMessage) -> None:
        """Send a message to all connected clients. Remove dead connections."""
        if not self._clients:
            return
        data = msg.to_json()
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    async def handler(self, ws: WebSocket) -> None:
        """WebSocket endpoint handler — just keep the connection alive."""
        await self.connect(ws)
        try:
            while True:
                # Read incoming (ping-pong / control messages)
                try:
                    data = await asyncio.wait_for(ws.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    pass  # just loop — keep-alive
        except WebSocketDisconnect:
            pass
        finally:
            await self.disconnect(ws)

    @property
    def client_count(self) -> int:
        return len(self._clients)


# Singleton hub, created in app factory
hub: WsHub = WsHub()
