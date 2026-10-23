"""
diagnosis/ensemble.py — Weighted ensemble of MLP-MCD, XGBoost, and evidence rules.

Combines three diagnosers:
  A  — MC-dropout MLP  (weight 0.45)
  B  — XGBoost         (weight 0.45)
  C  — Evidence rules  (weight 0.10)

UNKNOWN abstention rule (applied after weighted average):
  max(probs) < τ_abstain   OR
  (MLP label ≠ XGB label AND evidence rule is silent / returns UNKNOWN)

Outputs an EnsembleResult with:
  - predicted label (may be UNKNOWN)
  - class probability distribution (normalised)
  - per-model predictions for UI display
  - top feature attributions from XGB explain()
  - mlp_label / xgb_label / ev_label for trust signal E/D computation
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import numpy as np

from diagnosis.mlp_mcd import CLASS_ORDER, N_CLASSES, MCPrediction, get_classifier as get_mlp
from diagnosis.xgb import get_classifier as get_xgb
from diagnosis.evidence_rules import diagnose as ev_diagnose, confidence as ev_confidence
from telemetry.features import FeatureVector, FEATURE_NAMES

# Weights must sum to 1.0
_W_MLP = 0.45
_W_XGB = 0.45
_W_EV  = 0.10

# τ_abstain loaded from models/thresholds.json (set by training); default 0.55
_THRESHOLDS_PATH = os.path.join(os.path.dirname(__file__), "../../models/thresholds.json")

_tau_abstain: float | None = None


def _load_tau_abstain() -> float:
    global _tau_abstain
    if _tau_abstain is not None:
        return _tau_abstain
    if os.path.exists(_THRESHOLDS_PATH):
        try:
            with open(_THRESHOLDS_PATH) as f:
                data = json.load(f)
            _tau_abstain = float(data.get("tau_abstain", 0.30))
            return _tau_abstain
        except Exception:
            pass
    _tau_abstain = 0.30
    return _tau_abstain


def invalidate_tau_cache() -> None:
    """Call after training to reload thresholds from disk."""
    global _tau_abstain
    _tau_abstain = None


# ── Per-model predictions ────────────────────────────────────────────────────

@dataclass
class ModelPrediction:
    """Output of a single diagnoser."""
    label: str
    confidence: float
    probs: dict[str, float]        # label → probability


# ── Ensemble result ───────────────────────────────────────────────────────────

@dataclass
class EnsembleResult:
    """Full ensemble diagnosis for one service at one tick."""
    label: str                          # final predicted label (may be UNKNOWN)
    confidence: float                   # max ensemble probability
    probs: dict[str, float]             # label → weighted probability
    mlp: ModelPrediction | None = None
    xgb: ModelPrediction | None = None
    evidence: ModelPrediction | None = None
    top_features: dict[str, float] = field(default_factory=dict)
    # MC-dropout uncertainty (entropy of MLP mean distribution)
    mlp_uncertainty: float = 0.0
    # Flags used by trust signals
    abstained: bool = False             # UNKNOWN by abstention rule


def _uniform_probs() -> dict[str, float]:
    return {c: 1.0 / N_CLASSES for c in CLASS_ORDER}


def _label_to_probs_vec(label: str) -> np.ndarray:
    """Convert a single label to a one-hot (or near-uniform) prob vector."""
    if label == "UNKNOWN":
        return np.ones(N_CLASSES) / N_CLASSES
    idx = CLASS_ORDER.index(label) if label in CLASS_ORDER else -1
    if idx < 0:
        return np.ones(N_CLASSES) / N_CLASSES
    v = np.zeros(N_CLASSES)
    v[idx] = 1.0
    return v


def diagnose(fv: FeatureVector) -> EnsembleResult:
    """
    Run the full ensemble on a feature vector and return a diagnosis.
    Falls back gracefully when models are not yet trained.
    """
    x = fv.to_numpy()
    tau = _load_tau_abstain()

    # ── Model A: MLP MC-dropout ───────────────────────────────────────────────
    mlp_clf = get_mlp()
    mc: MCPrediction = mlp_clf.predict_proba_mc(x, n_passes=30)
    mlp_probs_vec = mc.mean_probs                    # (N_CLASSES,)
    mlp_pred = ModelPrediction(
        label=mc.label,
        confidence=mc.confidence,
        probs={CLASS_ORDER[i]: float(p) for i, p in enumerate(mlp_probs_vec)},
    )

    # ── Model B: XGBoost ──────────────────────────────────────────────────────
    xgb_clf = get_xgb()
    xgb_probs_dict = xgb_clf.predict_proba(x)
    xgb_probs_vec = np.array([xgb_probs_dict.get(c, 0.0) for c in CLASS_ORDER])
    xgb_label = CLASS_ORDER[int(np.argmax(xgb_probs_vec))]
    xgb_pred = ModelPrediction(
        label=xgb_label,
        confidence=float(np.max(xgb_probs_vec)),
        probs=xgb_probs_dict,
    )

    # ── Model C: Evidence rules ────────────────────────────────────────────────
    ev_label = ev_diagnose(fv)
    ev_conf  = ev_confidence(fv)
    ev_probs_vec = _label_to_probs_vec(ev_label) * ev_conf
    # Renormalise (one-hot × confidence → blend with uniform for remaining mass)
    # so sum stays 1.0
    if ev_label != "UNKNOWN":
        idx = CLASS_ORDER.index(ev_label)
        ev_vec = np.ones(N_CLASSES) * (1.0 - ev_conf) / (N_CLASSES - 1)
        ev_vec[idx] = ev_conf
    else:
        ev_vec = np.ones(N_CLASSES) / N_CLASSES
    ev_pred = ModelPrediction(
        label=ev_label,
        confidence=ev_conf,
        probs={CLASS_ORDER[i]: float(p) for i, p in enumerate(ev_vec)},
    )

    # ── Weighted blend ────────────────────────────────────────────────────────
    ensemble_vec = _W_MLP * mlp_probs_vec + _W_XGB * xgb_probs_vec + _W_EV * ev_vec
    # Normalise (should already sum to ~1.0 but guard against fp drift)
    total = ensemble_vec.sum()
    if total > 0:
        ensemble_vec = ensemble_vec / total

    best_idx = int(np.argmax(ensemble_vec))
    best_label = CLASS_ORDER[best_idx]
    best_conf  = float(ensemble_vec[best_idx])

    # ── UNKNOWN abstention rule ───────────────────────────────────────────────
    models_disagree = (mc.label != "UNKNOWN" and xgb_label != "UNKNOWN"
                       and mc.label != xgb_label)
    evidence_silent = ev_label == "UNKNOWN"
    abstained = (
        best_conf < tau
        or (models_disagree and evidence_silent)
        or best_label == "UNKNOWN"
    )
    if abstained:
        best_label = "UNKNOWN"
        best_conf  = float(ensemble_vec[CLASS_ORDER.index("UNKNOWN")])

    # ── Feature attributions from XGB ────────────────────────────────────────
    top_features: dict[str, float] = {}
    try:
        attributions = xgb_clf.explain(x)
        # Keep top-10 by absolute importance
        top_features = dict(
            sorted(attributions.items(), key=lambda kv: abs(kv[1]), reverse=True)[:10]
        )
    except Exception:
        pass

    return EnsembleResult(
        label=best_label,
        confidence=best_conf,
        probs={CLASS_ORDER[i]: float(p) for i, p in enumerate(ensemble_vec)},
        mlp=mlp_pred,
        xgb=xgb_pred,
        evidence=ev_pred,
        top_features=top_features,
        mlp_uncertainty=mc.uncertainty,
        abstained=abstained,
    )
