"""
diagnosis/evidence_rules.py — Deterministic evidence rule diagnoser.

Returns one of the 9 diagnosis labels based on hard feature signals.
Intentionally imperfect — designed to overlap with ML models on ambiguous
cases (cpu_stress vs traffic_surge, memory_exhaustion vs memory_leak).
"""

from __future__ import annotations

from telemetry.features import FeatureVector

LABELS = [
    "CPU_OVERLOAD",
    "MEMORY_EXHAUSTION",
    "MEMORY_LEAK",
    "TRAFFIC_OVERLOAD",
    "NETWORK_PARTITION",
    "BAD_DEPLOYMENT",
    "NODE_FAILURE",
    "NORMAL",
    "UNKNOWN",
]


def diagnose(fv: FeatureVector) -> str:
    """
    Apply deterministic evidence rules and return a diagnosis label.
    Returns UNKNOWN when no rule fires strongly.
    """
    fd = fv.as_dict()

    # ── Hard signals ──────────────────────────────────────────────────────────
    oom             = fd.get("last_termination_reason_oom", 0.0) > 0.5
    mem_slope       = fd.get("mem_ratio_mean_slope", 0.0)
    mem_last        = fd.get("mem_ratio_mean_last", 1.0)
    cpu_last        = fd.get("cpu_util_mean_last", 1.0)
    error_last      = fd.get("error_rate_last", 1.0)
    rr_ratio        = fd.get("request_rate_ratio_vs_baseline", 1.0)
    net_err_last    = fd.get("network_error_rate_last", 0.0)
    node_ready_frac = fd.get("node_ready_fraction", 1.0)
    unknown_pods    = fd.get("unknown_pods_count_normalised", 0.0)
    error_spike_rev = fd.get("error_spike_post_revision", 0.0) > 0.5
    backoff_events  = fd.get("event_BackOff_count_window", 0.0)
    restart_delta   = fd.get("restart_delta_last_tick", 0.0)

    # ── Rule 1: NODE_FAILURE — node not ready or many Unknown pods ────────────
    if node_ready_frac < 0.76 or unknown_pods > 0.2:
        return "NODE_FAILURE"

    # ── Rule 2: BAD_DEPLOYMENT — error spike right after a revision ───────────
    if error_spike_rev and error_last > 3.0:
        return "BAD_DEPLOYMENT"

    # ── Rule 3: MEMORY rules ──────────────────────────────────────────────────
    if oom or (backoff_events > 0 and restart_delta > 0):
        # OOM with rising memory slope → leak
        if mem_slope > 0.5:
            return "MEMORY_LEAK"
        return "MEMORY_EXHAUSTION"

    if mem_last > 1.8 and mem_slope > 0.3:
        return "MEMORY_LEAK"

    if mem_last > 1.6:
        return "MEMORY_EXHAUSTION"

    # ── Rule 4: NETWORK_PARTITION — elevated network error rate ───────────────
    if net_err_last > 5.0:
        return "NETWORK_PARTITION"

    # ── Rule 5: TRAFFIC_OVERLOAD vs CPU_OVERLOAD (overlapping symptoms) ───────
    # Both have high CPU and elevated errors.  The distinguishing signal is
    # request rate: traffic surge shows request_rate >> 2× baseline.
    if error_last > 3.0 or cpu_last > 1.8:
        if rr_ratio > 2.0:
            # High request rate: more likely traffic overload
            return "TRAFFIC_OVERLOAD"
        else:
            # CPU stress without proportional traffic rise: CPU overload
            return "CPU_OVERLOAD"

    # ── Rule 6: NORMAL — everything near baseline ─────────────────────────────
    if (cpu_last < 1.3 and mem_last < 1.3 and error_last < 2.0
            and net_err_last < 2.0 and rr_ratio < 1.5):
        return "NORMAL"

    return "UNKNOWN"


def confidence(fv: FeatureVector) -> float:
    """
    Return a rough confidence for the evidence rule diagnosis.
    0.9 for hard signals (OOM, node down), 0.6 for softer signals.
    """
    fd = fv.as_dict()
    label = diagnose(fv)
    if label == "UNKNOWN" or label == "NORMAL":
        return 0.5
    # Hard signals → high confidence
    if label in ("NODE_FAILURE", "BAD_DEPLOYMENT"):
        return 0.85
    if label == "MEMORY_EXHAUSTION" and fd.get("last_termination_reason_oom", 0) > 0.5:
        return 0.90
    return 0.65
