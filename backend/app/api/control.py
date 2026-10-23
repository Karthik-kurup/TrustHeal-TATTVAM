"""app/api/control.py - Autonomy mode, thresholds, limits, kill switch."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

router = APIRouter()


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModeRequest(ControlRequest):
    autonomy_stage: Literal["S0", "S1", "S2", "S3", "S4"]


class ThresholdRequest(ControlRequest):
    R1_auto: float | None = Field(default=None, ge=0.0, le=1.0)
    R2_auto: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_thresholds(self) -> "ThresholdRequest":
        if self.R1_auto is None and self.R2_auto is None:
            raise ValueError("At least one threshold must be provided")
        request = self.model_fields_set
        if any(getattr(self, field) is None for field in request):
            raise ValueError("Thresholds cannot be null")
        r1 = self.R1_auto if "R1_auto" in request else None
        r2 = self.R2_auto if "R2_auto" in request else None
        if r1 is not None and r2 is not None and r2 < r1:
            raise ValueError("R2_auto must be greater than or equal to R1_auto")
        return self


class LimitsRequest(ControlRequest):
    actions_per_service_per_hour: int | None = Field(default=None, ge=1, le=20)
    actions_per_cluster_per_hour: int | None = Field(default=None, ge=1, le=100)
    cooldown_seconds: float | None = Field(default=None, ge=0.0, le=300.0)

    @model_validator(mode="after")
    def require_a_limit(self) -> "LimitsRequest":
        fields = (
            "actions_per_service_per_hour",
            "actions_per_cluster_per_hour",
            "cooldown_seconds",
        )
        if not any(field in self.model_fields_set for field in fields):
            raise ValueError("At least one limit must be provided")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Limits cannot be null")
        return self


@router.get("/mode")
async def get_mode(request: Request):
    sim = request.app.state.sim
    return {"autonomy_stage": sim.control.autonomy_stage}


@router.put("/mode")
async def set_mode(body: ModeRequest, request: Request):
    sim = request.app.state.sim
    sim.control.autonomy_stage = body.autonomy_stage
    return {"ok": True, "autonomy_stage": body.autonomy_stage}


@router.get("/thresholds")
async def get_thresholds(request: Request):
    sim = request.app.state.sim
    return {
        "R1_auto": sim.control.p_r1_threshold,
        "R2_auto": sim.control.p_r2_threshold,
        "precision_target_R1": 0.95,
        "precision_target_R2": 0.99,
    }


@router.put("/thresholds")
async def set_thresholds(body: ThresholdRequest, request: Request):
    sim = request.app.state.sim
    r1 = body.R1_auto if body.R1_auto is not None else sim.control.p_r1_threshold
    r2 = body.R2_auto if body.R2_auto is not None else sim.control.p_r2_threshold
    if r2 < r1:
        raise HTTPException(422, "R2_auto must be greater than or equal to R1_auto")
    sim.control.p_r1_threshold = r1
    sim.control.p_r2_threshold = r2
    return {"ok": True, "thresholds": {
        "R1_auto": sim.control.p_r1_threshold,
        "R2_auto": sim.control.p_r2_threshold,
        "precision_target_R1": 0.95,
        "precision_target_R2": 0.99,
    }}


@router.get("/limits")
async def get_limits(request: Request):
    sim = request.app.state.sim
    return {
        "actions_per_service_per_hour": sim.control.service_budget,
        "actions_per_cluster_per_hour": sim.control.cluster_budget,
        "cooldown_seconds": sim.control.cooldown_seconds,
        "max_actions_per_service_per_hour": 20,
        "max_actions_per_cluster_per_hour": 100,
        "min_cooldown_seconds": 0,
        "max_cooldown_seconds": 300,
    }


@router.put("/limits")
async def set_limits(body: LimitsRequest, request: Request):
    sim = request.app.state.sim
    if body.actions_per_service_per_hour is not None:
        sim.control.service_budget = body.actions_per_service_per_hour
    if body.actions_per_cluster_per_hour is not None:
        sim.control.cluster_budget = body.actions_per_cluster_per_hour
    if body.cooldown_seconds is not None:
        sim.control.cooldown_seconds = body.cooldown_seconds
    sim.guardrails.update_limits(
        service_budget=sim.control.service_budget,
        cluster_budget=sim.control.cluster_budget,
        cooldown=sim.control.cooldown_seconds,
    )
    return {"ok": True, "limits": {
        "actions_per_service_per_hour": sim.control.service_budget,
        "actions_per_cluster_per_hour": sim.control.cluster_budget,
        "cooldown_seconds": sim.control.cooldown_seconds,
        "max_actions_per_service_per_hour": 20,
        "max_actions_per_cluster_per_hour": 100,
        "min_cooldown_seconds": 0,
        "max_cooldown_seconds": 300,
    }}


class KillSwitchRequest(BaseModel):
    enabled: bool


@router.post("/killswitch")
async def set_kill_switch(body: KillSwitchRequest, request: Request):
    sim = request.app.state.sim
    sim.control.kill_switch = body.enabled
    # Audit the kill switch toggle
    sim.audit.append(
        sim_time=sim.sim_time,
        event_type="kill_switch",
        incident_id="",
        service="",
        action="kill_switch",
        target="",
        risk_class="",
        gate_decision="",
        result="enabled" if body.enabled else "disabled",
        detail=f"Kill switch {'enabled' if body.enabled else 'disabled'}",
        autonomy_stage=sim.control.autonomy_stage,
    )
    return {"ok": True, "kill_switch": body.enabled}


@router.get("/state")
async def get_control_state(request: Request):
    sim = request.app.state.sim
    return {
        "autonomy_stage": sim.control.autonomy_stage,
        "kill_switch": sim.control.kill_switch,
        "thresholds": {
            "R1_auto": sim.control.p_r1_threshold,
            "R2_auto": sim.control.p_r2_threshold,
        },
        "limits": {
            "actions_per_service_per_hour": sim.control.service_budget,
            "actions_per_cluster_per_hour": sim.control.cluster_budget,
            "cooldown_seconds": sim.control.cooldown_seconds,
            "max_actions_per_service_per_hour": 20,
            "max_actions_per_cluster_per_hour": 100,
            "min_cooldown_seconds": 0,
            "max_cooldown_seconds": 300,
        },
    }
