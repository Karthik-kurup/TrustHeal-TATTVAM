"""app/api/metrics.py — Historical metrics query endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request, Query

router = APIRouter()

_METRIC_ATTR_MAP = {
    "cpu":         "cpu_util_mean",
    "cpu_util":    "cpu_util_mean",
    "mem":         "mem_ratio_mean",
    "mem_ratio":   "mem_ratio_mean",
    "latency_p50": "latency_p50",
    "latency_p95": "latency_p95",
    "error_rate":  "error_rate",
    "request_rate":"request_rate",
    "network_errors": "network_error_rate",
    "restarts":    "restart_count",
}


@router.get("/metrics/query")
async def query_metrics(
    request: Request,
    service: str = Query(...),
    metric: str = Query("cpu_util"),
    from_time: float = Query(0.0, alias="from"),
    to_time: float = Query(float("inf"), alias="to"),
    limit: int = Query(200),
):
    sim = request.app.state.sim
    attr = _METRIC_ATTR_MAP.get(metric, metric)

    samples = sim.telemetry.get_window(service, 200)
    result = []
    for s in samples:
        if s.sim_time < from_time or s.sim_time > to_time:
            continue
        val = getattr(s, attr, None)
        if val is None:
            continue
        result.append({"sim_time": s.sim_time, "value": val})
    return result[-limit:]
