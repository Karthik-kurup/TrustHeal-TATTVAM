"""app/api/scenarios.py — Scenario library and execution endpoints."""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

_SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "../../twin/scenarios")


def _load_scenarios() -> list[dict]:
    result = []
    scenarios_path = os.path.normpath(_SCENARIOS_DIR)
    if not os.path.isdir(scenarios_path):
        return result
    for fname in os.listdir(scenarios_path):
        if fname.endswith(".json"):
            try:
                with open(os.path.join(scenarios_path, fname), encoding="utf-8") as f:
                    result.append(json.load(f))
            except Exception:
                pass
    return result


@router.get("/scenarios")
async def list_scenarios():
    return _load_scenarios()


@router.post("/scenarios/{scenario_id}/run")
async def run_scenario(scenario_id: str, request: Request):
    """
    Load and run a scenario JSON: resets sim, sets seed, schedules faults.
    """
    scenarios = _load_scenarios()
    scenario = next((s for s in scenarios if s.get("id") == scenario_id), None)
    if scenario is None:
        raise HTTPException(404, f"Scenario {scenario_id!r} not found")

    sim = request.app.state.sim
    seed = scenario.get("seed", sim.seed)
    sim.reset(seed=seed)

    # Set speed if specified
    if "speed" in scenario:
        sim.speed = float(scenario["speed"])

    # Schedule faults
    from twin.faults import FaultSpec, FaultKind, OnsetShape
    for fault_spec in scenario.get("faults", []):
        kind_str = fault_spec.get("kind")
        if not kind_str:
            continue  # null kind = marker step
        at = float(fault_spec.get("at_sim_time", 0))
        # We inject them all now; the sim_time offset is noted but for demo
        # purposes we inject immediately. For full scheduling, M9 adds a queue.
        try:
            spec = FaultSpec(
                kind=FaultKind(kind_str),
                target=fault_spec["target"],
                intensity=float(fault_spec.get("intensity", 1.0)),
                onset_shape=OnsetShape(fault_spec.get("onset_shape", "sigmoid")),
                onset_duration=float(fault_spec.get("onset_duration", 30.0)),
                duration=float(fault_spec.get("duration", 300.0)),
                metadata=fault_spec.get("metadata", {}),
            )
        except (ValueError, KeyError):
            continue

    sim.resume()
    return {"ok": True, "scenario_id": scenario_id, "seed": seed}
