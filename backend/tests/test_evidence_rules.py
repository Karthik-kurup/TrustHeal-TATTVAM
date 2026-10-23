"""
tests/test_evidence_rules.py — Unit tests for the evidence rule diagnoser
and the ensemble UNKNOWN abstention rule.
"""

from __future__ import annotations

import numpy as np
import pytest

from telemetry.features import FeatureVector, FEATURE_NAMES


def _make_fv(overrides: dict[str, float]) -> FeatureVector:
    """Create a FeatureVector with baseline values, applying the given overrides."""
    # Start from a healthy baseline (all features at normal values)
    fdict: dict[str, float] = {name: 0.0 for name in FEATURE_NAMES}
    # Set sensible defaults that represent a healthy service
    defaults = {
        "cpu_util_mean_mean": 1.0,      # at baseline
        "cpu_util_mean_max": 1.1,
        "cpu_util_mean_std": 0.05,
        "cpu_util_mean_slope": 0.0,
        "cpu_util_mean_last": 1.0,
        "mem_ratio_mean_mean": 1.0,
        "mem_ratio_mean_max": 1.1,
        "mem_ratio_mean_std": 0.05,
        "mem_ratio_mean_slope": 0.0,
        "mem_ratio_mean_last": 1.0,
        "error_rate_mean": 1.0,
        "error_rate_max": 1.1,
        "error_rate_std": 0.05,
        "error_rate_slope": 0.0,
        "error_rate_last": 1.0,
        "request_rate_mean": 1.0,
        "request_rate_max": 1.0,
        "request_rate_std": 0.05,
        "request_rate_slope": 0.0,
        "request_rate_last": 1.0,
        "network_error_rate_mean": 1.0,
        "network_error_rate_max": 1.0,
        "network_error_rate_std": 0.05,
        "network_error_rate_slope": 0.0,
        "network_error_rate_last": 1.0,
        "node_ready_fraction": 1.0,
        "unknown_pods_count_normalised": 0.0,
        "request_rate_ratio_vs_baseline": 1.0,
        "hpa_desired_vs_current": 0.0,
        "event_OOMKilled_count_window": 0.0,
        "event_BackOff_count_window": 0.0,
        "event_NodeNotReady_count_window": 0.0,
        "restart_delta_last_tick": 0.0,
        "latency_p95_ratio_vs_baseline": 1.0,
        "error_spike_post_revision": 0.0,
        "request_rate_above_hpa_ceiling": 0.0,
        "symptom_overlap_score": 0.0,
        "last_termination_reason_oom": 0.0,
        "last_exit_code_normalised": 0.0,
        "minutes_since_last_revision": 60.0,
        "error_rate_change_post_rollout": 0.0,
    }
    fdict.update(defaults)
    fdict.update(overrides)
    features = [fdict[n] for n in FEATURE_NAMES]
    return FeatureVector(
        sim_time=300.0,
        service_id="test",
        features=features,
        feature_names=FEATURE_NAMES,
        window_start=0.0,
        window_end=300.0,
    )


# ── Evidence rules unit tests ─────────────────────────────────────────────────

class TestEvidenceRules:

    def test_normal_returns_normal(self):
        from diagnosis.evidence_rules import diagnose
        fv = _make_fv({})
        assert diagnose(fv) == "NORMAL"

    def test_oom_returns_memory_exhaustion(self):
        from diagnosis.evidence_rules import diagnose
        fv = _make_fv({
            "last_termination_reason_oom": 1.0,
            "mem_ratio_mean_last": 1.7,
            "mem_ratio_mean_slope": 0.1,   # not a strong leak slope
        })
        assert diagnose(fv) == "MEMORY_EXHAUSTION"

    def test_oom_with_high_slope_returns_memory_leak(self):
        from diagnosis.evidence_rules import diagnose
        fv = _make_fv({
            "last_termination_reason_oom": 1.0,
            "mem_ratio_mean_slope": 0.8,   # strong slope → leak
            "restart_delta_last_tick": 1.0,
            "event_BackOff_count_window": 1.0,
        })
        assert diagnose(fv) == "MEMORY_LEAK"

    def test_high_mem_last_with_slope_returns_leak(self):
        from diagnosis.evidence_rules import diagnose
        fv = _make_fv({
            "mem_ratio_mean_last": 2.0,
            "mem_ratio_mean_slope": 0.5,
        })
        assert diagnose(fv) == "MEMORY_LEAK"

    def test_high_mem_last_no_slope_returns_exhaustion(self):
        from diagnosis.evidence_rules import diagnose
        fv = _make_fv({
            "mem_ratio_mean_last": 1.8,
            "mem_ratio_mean_slope": 0.0,
        })
        assert diagnose(fv) == "MEMORY_EXHAUSTION"

    def test_node_failure_low_ready_fraction(self):
        from diagnosis.evidence_rules import diagnose
        fv = _make_fv({
            "node_ready_fraction": 0.5,   # < 0.75
        })
        assert diagnose(fv) == "NODE_FAILURE"

    def test_node_failure_unknown_pods(self):
        from diagnosis.evidence_rules import diagnose
        fv = _make_fv({
            "unknown_pods_count_normalised": 0.5,  # > 0.2
        })
        assert diagnose(fv) == "NODE_FAILURE"

    def test_bad_deployment_error_spike_after_revision(self):
        from diagnosis.evidence_rules import diagnose
        fv = _make_fv({
            "error_spike_post_revision": 1.0,
            "error_rate_last": 4.0,   # > 3.0
        })
        assert diagnose(fv) == "BAD_DEPLOYMENT"

    def test_network_partition_high_net_errors(self):
        from diagnosis.evidence_rules import diagnose
        fv = _make_fv({
            "network_error_rate_last": 7.0,  # > 5.0
        })
        assert diagnose(fv) == "NETWORK_PARTITION"

    def test_traffic_overload_high_request_rate(self):
        from diagnosis.evidence_rules import diagnose
        fv = _make_fv({
            "error_rate_last": 4.0,
            "request_rate_ratio_vs_baseline": 2.5,  # > 2.0 → traffic
        })
        assert diagnose(fv) == "TRAFFIC_OVERLOAD"

    def test_cpu_overload_high_cpu_no_traffic(self):
        from diagnosis.evidence_rules import diagnose
        fv = _make_fv({
            "cpu_util_mean_last": 2.0,
            "request_rate_ratio_vs_baseline": 1.2,  # not traffic surge
            "error_rate_last": 3.5,
        })
        assert diagnose(fv) == "CPU_OVERLOAD"

    def test_confidence_hard_signals_high(self):
        from diagnosis.evidence_rules import confidence
        fv = _make_fv({
            "last_termination_reason_oom": 1.0,
        })
        c = confidence(fv)
        assert c >= 0.85

    def test_confidence_normal_is_moderate(self):
        from diagnosis.evidence_rules import confidence
        fv = _make_fv({})
        c = confidence(fv)
        assert c <= 0.6


# ── Ensemble UNKNOWN abstention tests ─────────────────────────────────────────

class TestEnsembleAbstention:

    def test_ensemble_runs_without_error(self):
        """Ensemble must run and return a valid EnsembleResult (trained or not)."""
        from diagnosis.ensemble import diagnose as ens_diagnose
        from diagnosis.mlp_mcd import CLASS_ORDER
        fv = _make_fv({})
        result = ens_diagnose(fv)
        # Must return a valid label (UNKNOWN or a known class)
        assert result.label in CLASS_ORDER, f"Unexpected label: {result.label}"
        # abstained field must be a bool
        assert isinstance(result.abstained, bool)

    def test_ensemble_result_has_all_classes(self):
        """EnsembleResult.probs must have a key for every CLASS_ORDER entry."""
        from diagnosis.ensemble import diagnose as ens_diagnose
        from diagnosis.mlp_mcd import CLASS_ORDER
        fv = _make_fv({})
        result = ens_diagnose(fv)
        for cls in CLASS_ORDER:
            assert cls in result.probs, f"Missing class {cls} in probs"

    def test_ensemble_probs_sum_to_one(self):
        """Ensemble probabilities must sum to ~1.0."""
        from diagnosis.ensemble import diagnose as ens_diagnose
        fv = _make_fv({})
        result = ens_diagnose(fv)
        total = sum(result.probs.values())
        assert abs(total - 1.0) < 0.01, f"Probs sum to {total}"

    def test_ensemble_has_model_predictions(self):
        """EnsembleResult must have mlp, xgb, and evidence sub-predictions."""
        from diagnosis.ensemble import diagnose as ens_diagnose
        fv = _make_fv({})
        result = ens_diagnose(fv)
        assert result.mlp is not None
        assert result.xgb is not None
        assert result.evidence is not None

    def test_ensemble_evidence_matches_rules(self):
        """Evidence sub-prediction label must match evidence_rules.diagnose."""
        from diagnosis.ensemble import diagnose as ens_diagnose
        from diagnosis.evidence_rules import diagnose as ev_diagnose
        fv = _make_fv({
            "node_ready_fraction": 0.5,
        })
        result = ens_diagnose(fv)
        ev_label = ev_diagnose(fv)
        assert result.evidence is not None
        assert result.evidence.label == ev_label


# ── Ensemble model prediction class ──────────────────────────────────────────

class TestModelPrediction:

    def test_model_prediction_fields(self):
        from diagnosis.ensemble import ModelPrediction
        mp = ModelPrediction(label="CPU_OVERLOAD", confidence=0.8,
                             probs={"CPU_OVERLOAD": 0.8, "NORMAL": 0.2})
        assert mp.label == "CPU_OVERLOAD"
        assert mp.confidence == 0.8

    def test_ensemble_result_mlp_uncertainty_nonnegative(self):
        from diagnosis.ensemble import diagnose as ens_diagnose
        fv = _make_fv({})
        result = ens_diagnose(fv)
        assert result.mlp_uncertainty >= 0.0
