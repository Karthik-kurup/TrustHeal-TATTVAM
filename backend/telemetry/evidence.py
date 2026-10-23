"""
telemetry/evidence.py — Hard evidence feature extraction.

Extracts deterministic hard signals from a feature vector and telemetry window.
These feed directly into the evidence_rules diagnoser and the Trust Gate's E signal.
"""

from __future__ import annotations

from dataclasses import dataclass

from telemetry.features import FeatureVector, FEATURE_NAMES
from telemetry.store import TelemetrySample


@dataclass
class HardEvidence:
    """
    Hard binary/scalar signals extracted from telemetry.
    Each field has a clear physical meaning and no model uncertainty.
    """
    # OOM signals
    oom_terminated: bool        # any pod OOMKilled in window
    backoff_events: int         # CrashLoopBackOff events in window
    recent_restarts: int        # restart_delta_last_tick > 0

    # Deployment signals
    revision_changed_recently: bool   # last revision < 10 min ago
    error_spike_post_revision: bool   # error rate spiked after recent revision

    # Network / node signals
    node_not_ready: bool        # at least one node NotReady
    unknown_pods: int           # pods in Unknown phase

    # Resource signals
    cpu_ratio: float            # cpu_util_mean / baseline (from features, last stat)
    mem_ratio: float            # mem_ratio_mean / baseline (from features, last stat)
    request_rate_ratio: float   # request_rate / baseline

    # HPA signal
    hpa_undersized: bool        # hpa_desired > current replicas

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def extract_evidence(fv: FeatureVector,
                     window: list[TelemetrySample]) -> HardEvidence:
    """
    Derive hard evidence signals from a FeatureVector and the raw window samples.
    """
    fd = fv.as_dict()
    last = window[-1] if window else None

    # OOM/crash
    oom_terminated = fd.get("last_termination_reason_oom", 0.0) > 0.5
    backoff_count = int(fd.get("event_BackOff_count_window", 0.0))
    restart_delta = int(fd.get("restart_delta_last_tick", 0.0))

    # Deployment
    mins_since_rev = fd.get("minutes_since_last_revision", 999.0)
    revision_changed_recently = mins_since_rev < 10.0
    error_spike_post_revision = fd.get("error_spike_post_revision", 0.0) > 0.5

    # Node / pod status
    node_ready_fraction = fd.get("node_ready_fraction", 1.0)
    node_not_ready = node_ready_fraction < 1.0
    unknown_pods = int(round(fd.get("unknown_pods_count_normalised", 0.0) *
                             (last.replicas if last else 1)))

    # Resources (normalised "last" stats)
    cpu_ratio = fd.get("cpu_util_mean_last", 1.0)
    mem_ratio = fd.get("mem_ratio_mean_last", 1.0)
    request_rate_ratio = fd.get("request_rate_ratio_vs_baseline", 1.0)

    # HPA
    hpa_gap = fd.get("hpa_desired_vs_current", 0.0)
    hpa_undersized = hpa_gap > 0.5  # desired > current

    return HardEvidence(
        oom_terminated=oom_terminated,
        backoff_events=backoff_count,
        recent_restarts=restart_delta,
        revision_changed_recently=revision_changed_recently,
        error_spike_post_revision=error_spike_post_revision,
        node_not_ready=node_not_ready,
        unknown_pods=unknown_pods,
        cpu_ratio=cpu_ratio,
        mem_ratio=mem_ratio,
        request_rate_ratio=request_rate_ratio,
        hpa_undersized=hpa_undersized,
    )
