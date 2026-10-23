"""
detection/rules.py — Threshold rule detector.

Returns an anomaly score [0, 1] based on how many normalised feature
thresholds are exceeded. Score 1.0 = strongly anomalous.
"""

from __future__ import annotations

from telemetry.features import FeatureVector

# Each rule: (feature_name, threshold, weight)
# Feature values are normalised ratios (1.0 = at baseline).
_RULES: list[tuple[str, float, float]] = [
    ("error_rate_last",          5.0,  0.35),
    ("cpu_util_mean_last",       1.8,  0.20),
    ("mem_ratio_mean_last",      1.6,  0.20),
    ("latency_p95_last",         3.0,  0.15),
    ("restart_delta_last_tick",  0.5,  0.05),
    ("network_error_rate_last",  5.0,  0.05),
]

_TOTAL_WEIGHT = sum(w for _, _, w in _RULES)


def score(fv: FeatureVector) -> float:
    """
    Return anomaly score [0, 1].
    0 = all metrics at baseline, 1 = all rules strongly triggered.
    """
    fd = fv.as_dict()
    total = 0.0
    for feat, threshold, weight in _RULES:
        val = fd.get(feat, 0.0)
        if val >= threshold:
            # Partial credit: how far above the threshold (capped at 3×)
            excess = min((val - threshold) / max(threshold, 1e-9), 3.0) / 3.0
            total += weight * (0.5 + 0.5 * excess)
    return min(1.0, total / _TOTAL_WEIGHT)


def is_anomalous(fv: FeatureVector, threshold: float = 0.3) -> bool:
    return score(fv) >= threshold
