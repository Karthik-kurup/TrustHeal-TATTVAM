"""app/api/actions.py — Manual healing action endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Any

router = APIRouter()


class ManualActionRequest(BaseModel):
    action: str
    target: str
    dry_run: bool = False
    params: dict[str, Any] = {}


@router.post("/actions/manual")
async def manual_action(body: ManualActionRequest, request: Request):
    sim = request.app.state.sim
    if body.dry_run:
        result = sim.adapter.dry_run(body.action, body.target, body.params)
    else:
        result = sim.adapter.execute(body.action, body.target, body.params)
    return result
