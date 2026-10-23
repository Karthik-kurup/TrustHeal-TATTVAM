"""
trust/signals.py - Six trust signals: U, S, N, H, E, D.

U - MC-dropout uncertainty (entropy of MLP mean distribution, normalised)
S - Attribution stability (rank correlation of top-k features under small perturbations)
N - Novelty / OOD detection (Mahalanobis distance, normalised to [0,1])
H - Health agreement (1 if health is still BAD and fault class is not NORMAL/UNKNOWN)
E - Evidence agreement between ML ensemble and deterministic rules
    1.0 = confirm (same label), 0.5 = silent (evidence says UNKNOWN), 0.0 = contradict
D - Diagnoser agreement between MLP and XGB
    1.0 = same label, 0.0 = different label (both non-UNKNOWN)

All signals are in [0, 1] where higher = more trustworthy EXCEPT U and N
where higher = less trustworthy. The gate normalises appropriately.
"""

from __future__ import annotations

import json
import math
import os

import numpy as np

from diagnosis.ensemble import EnsembleResult
from telemetry.features import FeatureVector
from telemetry.health import HealthStatus

_THRESHOLDS_PATH = os.path.join(os.path.dirname(__file__), "../../models/thresholds.json")

_MAX_ENTROPY = math.log(9)   # max entropy for 9-class distribution


def _load_thresholds() -> dict:
    if os.path.exists(_THRESHOLDS_PATH):
        try:
            with open(_THRESHOLDS_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def compute_U(result: EnsembleResult) -> float:
    """
    U: MC-dropout uncertainty, normalised to [0,1].
    Higher = more uncertain.
    Uses entropy of MLP mean distribution / max_entropy.
    """
    entropy = result.mlp_uncertainty   # already computed in MCPrediction
    return float(min(1.0, entropy / max(_MAX_ENTROPY, 1e-9)))


def compute_S(fv: FeatureVector, result: EnsembleResult,
              n_perturbations: int = 5, sigma: float = 0.05) -> float:
    """
    S: Attribution stability under small Gaussian perturbations.
    Measures rank correlation of top-k feature attributions.
    Higher S = more stable = more trustworthy.

    Fast approximation: perturb the feature vector n times and measure
    how consistently the top-5 features stay in the top-5.
    """
    if not result.top_features:
        return 0.5   # no attributions available

    original_ranks = {k: i for i, k in
                      enumerate(sorted(result.top_features,
                                       key=lambda k: -abs(result.top_features[k])))}
    top_k_original = set(list(original_ranks.keys())[:5])

    from diagnosis.xgb import get_classifier as get_xgb
    from telemetry.features import FEATURE_NAMES

    xgb_clf = get_xgb()
    if not xgb_clf._fitted:
        return 0.5

    x = fv.to_numpy()
    overlaps = []
    rng = np.random.RandomState(0)
    for _ in range(n_perturbations):
        noise = rng.normal(0, sigma, x.shape).astype(np.float32)
        x_perturbed = x + noise
        try:
            attrib = xgb_clf.explain(x_perturbed)
            if not attrib:
                overlaps.append(0.5)
                continue
            top_k_perturbed = set(
                sorted(attrib, key=lambda k: -abs(attrib[k]))[:5]
            )
            overlap = len(top_k_original & top_k_perturbed) / max(len(top_k_original), 1)
            overlaps.append(overlap)
        except Exception:
            overlaps.append(0.5)

    return float(np.mean(overlaps)) if overlaps else 0.5


def compute_N(fv: FeatureVector) -> float:
    """
    N: Novelty score (OOD).
    Uses Mahalanobis distance from training distribution (diagonal covariance).
    Normalised by the p99 threshold so N=1 means at or beyond the OOD boundary.
    Higher N = more OOD = less trustworthy.
    """
    thresholds = _load_thresholds()
    mu = thresholds.get("ood_mu")
    var = thresholds.get("ood_var")
    threshold = thresholds.get("ood_mahalanobis_threshold", 30.0)

    if mu is None or var is None:
        return 0.5   # fallback: unknown novelty

    mu_arr  = np.array(mu, dtype=np.float32)
    var_arr = np.array(var, dtype=np.float32) + 1e-6
    x = fv.to_numpy()
    dist = float(np.sqrt(((x - mu_arr) ** 2 / var_arr).sum()))
    # Normalise: 0 = perfectly in-distribution, 1 = at the OOD threshold
    return float(min(1.0, dist / max(threshold, 1e-6)))


def compute_H(result: EnsembleResult, health_status: HealthStatus) -> float:
    """
    H: Health agreement.
    1.0 if health is still BAD and diagnosis is not NORMAL/UNKNOWN.
    0.5 if health is BAD but diagnosis is NORMAL or UNKNOWN.
    0.0 if health is OK (service recovered before action taken).
    """
    if health_status == HealthStatus.BAD:
        if result.label not in ("NORMAL", "UNKNOWN"):
            return 1.0
        return 0.5
    return 0.0


def compute_E(result: EnsembleResult) -> float:
    """
    E: Evidence-ML agreement.
    1.0 = evidence rule and ensemble agree on same non-UNKNOWN label
    0.5 = evidence rule is silent (returns UNKNOWN)
    0.0 = evidence rule contradicts ensemble
    """
    if result.evidence is None:
        return 0.5
    ev_label = result.evidence.label
    ml_label = result.label

    if ev_label == "UNKNOWN":
        return 0.5
    if ev_label == ml_label:
        return 1.0
    return 0.0


def compute_D(result: EnsembleResult) -> float:
    """
    D: MLP-XGB diagnoser agreement.
    1.0 = both agree on same non-UNKNOWN label
    0.5 = one or both is UNKNOWN
    0.0 = both non-UNKNOWN but different
    """
    if result.mlp is None or result.xgb is None:
        return 0.5
    mlp_label = result.mlp.label
    xgb_label = result.xgb.label

    if mlp_label == "UNKNOWN" or xgb_label == "UNKNOWN":
        return 0.5
    if mlp_label == xgb_label:
        return 1.0
    return 0.0


def compute_all(
    fv: FeatureVector,
    result: EnsembleResult,
    health_status: HealthStatus,
) -> dict[str, float]:
    """Compute all six signals and return a dict."""
    return {
        "U": compute_U(result),
        "S": compute_S(fv, result),
        "N": compute_N(fv),
        "H": compute_H(result, health_status),
        "E": compute_E(result),
        "D": compute_D(result),
    }
