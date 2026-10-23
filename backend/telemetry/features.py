"""
telemetry/features.py — 64-feature window vector.

Exactly 64 features per the master plan §8:
  - 9 metrics × 5 stats = 45  (normalised relative to service's own baseline)
  - 19 context / evidence features

All stats are normalised: value / baseline_mean (so 1.0 = at baseline).
Slope is in units of (metric_ratio / tick) — rising = > 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from telemetry.store import TelemetrySample, TelemetryStore, WINDOW_TICKS, BASELINE_TICKS

# ── Feature layout ────────────────────────────────────────────────────────────

# 9 raw metric names extracted from TelemetrySample
_METRIC_NAMES = [
    "cpu_util_mean",
    "mem_ratio_mean",
    "latency_p50",
    "latency_p95",
    "error_rate",
    "request_rate",
    "network_error_rate",
    "ready_ratio",       # ready_replicas / replicas  (derived)
    "restart_count",
]

_STAT_NAMES = ["mean", "max", "std", "slope", "last"]

# 9 × 5 = 45 per-metric features
_N_METRIC_FEATURES = len(_METRIC_NAMES) * len(_STAT_NAMES)  # 45

# 19 context features (listed below in order)
_CONTEXT_NAMES = [
    "last_termination_reason_oom",    # 1 if OOMKilled else 0
    "last_exit_code_normalised",      # exit_code / 255
    "minutes_since_last_revision",    # (sim_time - last_revision_time) / 60
    "error_rate_change_post_rollout", # error_rate_last - error_rate_at_revision
    "node_ready_fraction",            # ready_nodes / total_nodes
    "unknown_pods_count_normalised",  # unknown_pods / replicas
    "request_rate_ratio_vs_baseline", # request_rate_last / baseline_request_rate
    "hpa_desired_vs_current",         # hpa_desired - replicas  (0 if HPA disabled)
    "event_OOMKilled_count_window",   # OOMKilled events in window
    "event_BackOff_count_window",     # BackOff events in window
    "event_NodeNotReady_count_window",# NodeNotReady events in window
    "detector_rules_score",           # filled in by detection layer (default 0)
    "detector_iforest_score",         # filled in by detection layer (default 0)
    "detector_autoencoder_score",     # filled in by detection layer (default 0)
    "restart_delta_last_tick",        # restart_delta of last sample
    "latency_p95_ratio_vs_baseline",  # latency_p95_last / baseline_latency_p95
    "error_spike_post_revision",      # 1 if error > 2× baseline within 10 min of revision
    "request_rate_above_hpa_ceiling", # 1 if request_rate > estimated max HPA can handle
    "symptom_overlap_score",          # placeholder; filled by ensemble after training
]

_N_CONTEXT_FEATURES = len(_CONTEXT_NAMES)  # 19

FEATURE_COUNT = _N_METRIC_FEATURES + _N_CONTEXT_FEATURES  # must == 64
assert FEATURE_COUNT == 64, f"Feature count = {FEATURE_COUNT}, expected 64"

FEATURE_NAMES: list[str] = (
    [f"{m}_{s}" for m in _METRIC_NAMES for s in _STAT_NAMES]
    + _CONTEXT_NAMES
)
assert len(FEATURE_NAMES) == 64


# ── Baseline cache ────────────────────────────────────────────────────────────

@dataclass
class ServiceBaseline:
    """Cached healthy-baseline statistics per service."""
    cpu_util_mean: float = 0.3
    mem_ratio_mean: float = 0.25
    latency_p50: float = 0.005
    latency_p95: float = 0.010
    error_rate: float = 0.001
    request_rate: float = 50.0
    network_error_rate: float = 0.001
    ready_ratio: float = 1.0
    restart_count: float = 0.0
    # Extra for context features
    latency_p95_for_context: float = 0.010


class BaselineCache:
    """Computes and caches per-service baselines from the first BASELINE_TICKS samples."""

    def __init__(self) -> None:
        self._cache: dict[str, ServiceBaseline] = {}

    def update(self, service_id: str, samples: list[TelemetrySample]) -> None:
        """Recompute baseline from provided samples (called once baseline window is full)."""
        if not samples:
            return

        def _mean(attr: str) -> float:
            vals = [getattr(s, attr) for s in samples]
            return float(np.mean(vals)) if vals else 0.0

        def _mean_ratio(samples: list[TelemetrySample]) -> float:
            vals = [
                s.ready_replicas / max(s.replicas, 1) for s in samples
            ]
            return float(np.mean(vals)) if vals else 1.0

        b = ServiceBaseline(
            cpu_util_mean=max(_mean("cpu_util_mean"), 1e-6),
            mem_ratio_mean=max(_mean("mem_ratio_mean"), 1e-6),
            latency_p50=max(_mean("latency_p50"), 1e-6),
            latency_p95=max(_mean("latency_p95"), 1e-6),
            error_rate=max(_mean("error_rate"), 1e-6),
            request_rate=max(_mean("request_rate"), 1e-3),
            network_error_rate=max(_mean("network_error_rate"), 1e-6),
            ready_ratio=max(_mean_ratio(samples), 1e-6),
            restart_count=max(_mean("restart_count"), 1e-6),
            latency_p95_for_context=max(_mean("latency_p95"), 1e-6),
        )
        self._cache[service_id] = b

    def get(self, service_id: str) -> ServiceBaseline:
        return self._cache.get(service_id, ServiceBaseline())

    def has(self, service_id: str) -> bool:
        return service_id in self._cache


# ── Feature extraction helpers ────────────────────────────────────────────────

def _extract_series(samples: list[TelemetrySample],
                    attr: str) -> np.ndarray:
    """Extract a numeric attribute from a list of samples as a 1-D array."""
    if attr == "ready_ratio":
        return np.array([
            s.ready_replicas / max(s.replicas, 1) for s in samples
        ], dtype=float)
    return np.array([getattr(s, attr) for s in samples], dtype=float)


def _stats(series: np.ndarray, baseline_val: float) -> list[float]:
    """
    Compute [mean, max, std, slope, last] normalised by baseline_val.
    Returns raw floats (no NaN).
    """
    if len(series) == 0:
        return [0.0, 0.0, 0.0, 0.0, 0.0]
    bv = max(abs(baseline_val), 1e-9)
    norm = series / bv
    mean_v = float(np.mean(norm))
    max_v = float(np.max(norm))
    std_v = float(np.std(norm))
    # Linear slope via least-squares fit over tick indices
    if len(norm) >= 2:
        x = np.arange(len(norm), dtype=float)
        slope_v = float(np.polyfit(x, norm, 1)[0])
    else:
        slope_v = 0.0
    last_v = float(norm[-1])
    return [mean_v, max_v, std_v, slope_v, last_v]


def _count_events(store: TelemetryStore, service_id: str,
                  event_reason: str, window_start: float) -> int:
    """Count events of a given reason for this service within the window."""
    count = 0
    for evt in store.recent_events(200):
        if (hasattr(evt, "sim_time") and evt.sim_time >= window_start
                and evt.reason.value == event_reason
                and evt.source.startswith(service_id)):
            count += 1
    return count


# ── Main feature computer ─────────────────────────────────────────────────────

class FeatureEngine:
    """
    Computes the 64-feature vector for a service given its telemetry window.
    Also maintains baseline caches per service.
    """

    def __init__(self) -> None:
        self.baseline_cache = BaselineCache()
        # Track error_rate at last revision per service for context feature
        self._error_at_revision: dict[str, float] = {}
        # Track whether revision changed for context feature
        self._last_seen_revision: dict[str, str] = {}

    def update_baseline(self, service_id: str,
                        store: TelemetryStore) -> None:
        """Call once BASELINE_TICKS samples are available."""
        if not self.baseline_cache.has(service_id):
            baseline_samples = store.get_baseline_window(service_id)
            if len(baseline_samples) >= BASELINE_TICKS:
                self.baseline_cache.update(service_id, baseline_samples)

    def compute(self, service_id: str, store: TelemetryStore,
                detector_scores: dict | None = None) -> "FeatureVector | None":
        """
        Compute the 64-feature vector for a service.
        Returns None if the window is not yet full.
        """
        if not store.has_full_window(service_id):
            return None

        # Ensure baseline exists
        self.update_baseline(service_id, store)
        baseline = self.baseline_cache.get(service_id)

        window = store.get_window(service_id, WINDOW_TICKS)
        if len(window) < WINDOW_TICKS:
            return None

        sim_time = window[-1].sim_time
        window_start = window[0].sim_time

        # ── Per-metric stats (45 features) ───────────────────────────────────
        metric_features: list[float] = []
        baseline_vals = {
            "cpu_util_mean":     baseline.cpu_util_mean,
            "mem_ratio_mean":    baseline.mem_ratio_mean,
            "latency_p50":       baseline.latency_p50,
            "latency_p95":       baseline.latency_p95,
            "error_rate":        baseline.error_rate,
            "request_rate":      baseline.request_rate,
            "network_error_rate":baseline.network_error_rate,
            "ready_ratio":       baseline.ready_ratio,
            "restart_count":     baseline.restart_count,
        }
        for metric in _METRIC_NAMES:
            series = _extract_series(window, metric)
            bv = baseline_vals[metric]
            metric_features.extend(_stats(series, bv))

        # ── Context features (19) ─────────────────────────────────────────────
        last = window[-1]

        # 1. last_termination_reason_oom
        f1 = 1.0 if last.last_termination_reason == "OOMKilled" else 0.0

        # 2. last_exit_code_normalised
        f2 = (last.last_exit_code or 0) / 255.0

        # 3. minutes_since_last_revision
        f3 = (sim_time - last.last_revision_time) / 60.0

        # 4. error_rate_change_post_rollout
        # Track revision changes
        prev_rev = self._last_seen_revision.get(service_id, last.revision)
        if last.revision != prev_rev:
            self._error_at_revision[service_id] = last.error_rate
            self._last_seen_revision[service_id] = last.revision
        elif service_id not in self._last_seen_revision:
            self._last_seen_revision[service_id] = last.revision

        error_at_rev = self._error_at_revision.get(service_id, baseline.error_rate)
        f4 = (last.error_rate - error_at_rev) / max(baseline.error_rate, 1e-6)

        # 5. node_ready_fraction
        f5 = (last.ready_node_count / max(last.total_node_count, 1))

        # 6. unknown_pods_count_normalised
        f6 = last.unknown_pod_count / max(last.replicas, 1)

        # 7. request_rate_ratio_vs_baseline
        f7 = last.request_rate / max(baseline.request_rate, 1e-3)

        # 8. hpa_desired_vs_current
        f8 = float((last.hpa_desired or last.replicas) - last.replicas)

        # 9-11. event counts in window
        f9  = float(_count_events(store, service_id, "OOMKilled",  window_start))
        f10 = float(_count_events(store, service_id, "BackOff",    window_start))
        f11 = float(_count_events(store, service_id, "NodeNotReady", window_start))

        # 12-14. detector scores (filled by detection layer, default 0)
        ds = detector_scores or {}
        f12 = float(ds.get("rules", 0.0))
        f13 = float(ds.get("iforest", 0.0))
        f14 = float(ds.get("autoencoder", 0.0))

        # 15. restart_delta_last_tick
        f15 = float(last.restart_delta)

        # 16. latency_p95_ratio_vs_baseline
        f16 = last.latency_p95 / max(baseline.latency_p95_for_context, 1e-6)

        # 17. error_spike_post_revision (bool)
        # True if error_rate > 2× baseline AND revision changed within last 10 min
        mins_since_rev = (sim_time - last.last_revision_time) / 60.0
        f17 = 1.0 if (
            last.error_rate > 2.0 * baseline.error_rate
            and mins_since_rev < 10.0
        ) else 0.0

        # 18. request_rate_above_hpa_ceiling
        # Estimate: if request_rate > baseline × max_replicas / current_replicas
        # this means even scaling up won't fully fix it
        # Simplified: if request_rate_ratio > 2.5 (far above even max HPA can serve)
        f18 = 1.0 if f7 > 2.5 else 0.0

        # 19. symptom_overlap_score (set to 0.0 before training; filled post-training)
        f19 = 0.0

        context_features = [
            f1, f2, f3, f4, f5, f6, f7, f8,
            f9, f10, f11, f12, f13, f14, f15, f16, f17, f18, f19,
        ]
        assert len(context_features) == 19

        all_features = metric_features + context_features
        assert len(all_features) == 64, f"Got {len(all_features)} features"

        return FeatureVector(
            sim_time=sim_time,
            service_id=service_id,
            features=all_features,
            feature_names=FEATURE_NAMES,
            window_start=window_start,
            window_end=sim_time,
        )


# ── FeatureVector data class ──────────────────────────────────────────────────

@dataclass
class FeatureVector:
    """The 64-feature window vector for one service at one point in time."""
    sim_time: float
    service_id: str
    features: list[float]       # length 64
    feature_names: list[str]    # length 64
    window_start: float
    window_end: float

    def to_numpy(self) -> np.ndarray:
        return np.array(self.features, dtype=np.float32)

    def as_dict(self) -> dict[str, float]:
        return dict(zip(self.feature_names, self.features))
