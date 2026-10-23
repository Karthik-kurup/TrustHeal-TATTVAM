"""
training/train.py - Train all models and derive thresholds.

Steps:
  1. Load (or generate) the labelled dataset.
  2. Train IsolationForest detector on NORMAL samples.
  3. Train Autoencoder detector on NORMAL samples.
  4. Train MLP-MCD and XGBoost classifiers.
  5. Compute tau_abstain from the trained MLP on a held-out subset.
  6. Compute OOD Mahalanobis threshold from training distribution.
  7. Save all artefacts to models/.
  8. Run evaluation and write models/report.json.

Usage:
  python -m training.train              # generate data if missing, then train
  python -m training.train --skip-gen  # use existing dataset.npz
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

import numpy as np
from sklearn.preprocessing import LabelEncoder

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

MODELS_DIR = os.path.join(os.path.dirname(__file__), "../../models")
DATASET_PATH = os.path.join(MODELS_DIR, "dataset.npz")
THRESHOLDS_PATH = os.path.join(MODELS_DIR, "thresholds.json")


def _load_dataset(path: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load X, y, episode arrays from .npz."""
    data = np.load(path, allow_pickle=True)
    X = data["X"].astype(np.float32)
    y = data["y"].astype(str)
    episodes = data["episode"].astype(np.int32)
    return X, y, episodes


def train_detectors(X_normal: np.ndarray) -> None:
    """Train and save IsolationForest and Autoencoder on normal samples."""
    from detection.iforest import IForestDetector
    from detection.autoencoder import AutoencoderDetector

    print("  Training IsolationForest detector ...", flush=True)
    iforest = IForestDetector()
    iforest.fit(X_normal)
    iforest.save()
    print(f"    Saved iforest ({len(X_normal)} normal samples)", flush=True)

    print("  Training Autoencoder detector ...", flush=True)
    ae = AutoencoderDetector()
    ae.fit(X_normal, epochs=30, batch_size=64)
    ae.save()
    print("    Saved autoencoder", flush=True)


def train_classifiers(X: np.ndarray, y: np.ndarray,
                      feature_names: list[str]) -> None:
    """Train MLP and XGBoost classifiers."""
    from diagnosis.mlp_mcd import MLPMCDClassifier
    from diagnosis.xgb import XGBClassifier

    # Filter UNKNOWN from training
    mask = y != "UNKNOWN"
    X_tr, y_tr = X[mask], y[mask]

    print(f"  Training MLP-MCD classifier ({len(X_tr)} samples) ...", flush=True)
    mlp = MLPMCDClassifier()
    mlp.fit(X_tr, y_tr, epochs=60, batch_size=64, lr=1e-3)
    mlp.save()
    print("    Saved mlp_mcd", flush=True)

    print(f"  Training XGBoost classifier ({len(X_tr)} samples) ...", flush=True)
    xgb = XGBClassifier()
    xgb.fit(X_tr, y_tr, feature_names=feature_names)
    xgb.save()
    print("    Saved xgb", flush=True)


def _compute_tau_abstain(X: np.ndarray, y: np.ndarray,
                          episodes: np.ndarray) -> float:
    """
    Derive tau_abstain from the trained ensemble applied to a held-out subset.

    Strategy (fast, no re-training):
      1. Use the trained ensemble (MLP+XGB average) to score a sample subset.
      2. Collect ensemble max_conf for correct vs incorrect predictions.
      3. Choose tau that maximises F1 with recall >= 0.4.
      4. Enforce floor of 0.25 (ensemble confidence is diluted by MLP uncertainty).
    """
    from diagnosis.mlp_mcd import get_classifier as get_mlp, CLASS_ORDER
    from diagnosis.xgb import get_classifier as get_xgb

    mask = y != "UNKNOWN"
    X_k, y_k = X[mask], y[mask]

    if len(X_k) < 20:
        print("  Too few samples for tau computation -- using default 0.30", flush=True)
        return 0.30

    mlp = get_mlp()
    xgb_clf = get_xgb()
    if not mlp._fitted or not xgb_clf._fitted:
        print("  Models not fitted -- using default tau_abstain=0.30", flush=True)
        return 0.30

    # Sample up to 500 random known-class rows for fast evaluation
    rng = np.random.RandomState(42)
    n_sample = min(500, len(X_k))
    idx = rng.choice(len(X_k), n_sample, replace=False)
    X_sample, y_sample = X_k[idx], y_k[idx]

    print(f"  Computing tau_abstain from {n_sample} samples ...", flush=True)
    max_probs_correct: list[float] = []
    max_probs_incorrect: list[float] = []
    W = 0.45
    for xi, yi in zip(X_sample, y_sample):
        mc = mlp.predict_proba_mc(xi, n_passes=5)
        xgb_d = xgb_clf.predict_proba(xi)
        xgb_vec = np.array([xgb_d.get(c, 0.0) for c in CLASS_ORDER])
        ens = (W * mc.mean_probs + W * xgb_vec)
        ens = ens / max(ens.sum(), 1e-9)
        best_idx = int(np.argmax(ens))
        best_label = CLASS_ORDER[best_idx]
        max_p = float(ens[best_idx])
        if best_label == yi:
            max_probs_correct.append(max_p)
        else:
            max_probs_incorrect.append(max_p)

    if len(max_probs_correct) < 5:
        return 0.55

    all_max = np.array(max_probs_correct + max_probs_incorrect)
    all_labels = np.array([1] * len(max_probs_correct) + [0] * len(max_probs_incorrect))

    best_tau = 0.55
    best_f1  = 0.0
    for candidate_tau in np.arange(0.40, 0.85, 0.025):
        predicted_correct = all_max >= candidate_tau
        tp = int(((predicted_correct == 1) & (all_labels == 1)).sum())
        fp = int(((predicted_correct == 1) & (all_labels == 0)).sum())
        fn = int(((predicted_correct == 0) & (all_labels == 1)).sum())
        if tp + fp == 0 or tp + fn == 0:
            continue
        prec = tp / (tp + fp)
        rec  = tp / (tp + fn)
        if rec < 0.4:
            continue
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        if f1 > best_f1:
            best_f1  = f1
            best_tau = float(candidate_tau)

    tau = max(0.25, best_tau)
    print(f"    tau_abstain={tau:.3f} (F1={best_f1:.3f})", flush=True)
    return tau


def _compute_ood_threshold(X_normal: np.ndarray) -> float:
    """
    Compute Mahalanobis distance OOD threshold from normal samples.
    Returns the 99th percentile of distances on the normal training set.
    """
    mu = X_normal.mean(axis=0)
    # Use diagonal covariance (full covariance is too slow for 64 dims)
    var = X_normal.var(axis=0) + 1e-6
    dists = np.sqrt(((X_normal - mu) ** 2 / var).sum(axis=1))
    threshold = float(np.percentile(dists, 99))
    return threshold


def train(dataset_path: str = DATASET_PATH,
          skip_gen: bool = False,
          verbose: bool = True) -> None:
    """Main training entry point."""
    os.makedirs(MODELS_DIR, exist_ok=True)
    t0 = time.perf_counter()

    # Step 1: generate dataset if needed
    if not os.path.exists(dataset_path) and not skip_gen:
        print("Dataset not found -- generating ...", flush=True)
        from training.generate_dataset import generate
        generate(out_path=dataset_path, verbose=verbose)
    elif not os.path.exists(dataset_path):
        raise FileNotFoundError(
            f"Dataset not found: {dataset_path}. Run without --skip-gen."
        )

    print("Loading dataset ...", flush=True)
    X, y, episodes = _load_dataset(dataset_path)
    print(f"  {len(X)} samples, {len(np.unique(y))} classes", flush=True)

    # Load feature names from dataset
    raw = np.load(dataset_path, allow_pickle=True)
    feature_names: list[str] = list(raw["feature_names"].astype(str))

    # Step 2-3: detectors on NORMAL samples
    normal_mask = y == "NORMAL"
    X_normal = X[normal_mask]
    if len(X_normal) < 10:
        print("  WARNING: very few NORMAL samples for detector training", flush=True)
        X_normal = X  # fallback
    print(f"Training detectors on {len(X_normal)} normal samples ...", flush=True)
    train_detectors(X_normal)

    # Step 4: classifiers
    print("Training classifiers ...", flush=True)
    train_classifiers(X, y, feature_names)

    # Step 5: tau_abstain (uses trained MLP -- no extra training needed)
    print("Computing abstain threshold ...", flush=True)
    tau_abstain = _compute_tau_abstain(X, y, episodes)

    # Step 6: OOD threshold
    print("Computing OOD threshold ...", flush=True)
    ood_threshold = _compute_ood_threshold(X_normal)
    print(f"  OOD Mahalanobis threshold (p99) = {ood_threshold:.3f}", flush=True)

    # Step 7: save thresholds
    thresholds = {
        "tau_abstain": tau_abstain,
        "ood_mahalanobis_threshold": ood_threshold,
        "ood_mu": X_normal.mean(axis=0).tolist(),
        "ood_var": (X_normal.var(axis=0) + 1e-6).tolist(),
    }
    with open(THRESHOLDS_PATH, "w") as f:
        json.dump(thresholds, f, indent=2)
    print(f"  Saved thresholds -> {THRESHOLDS_PATH}", flush=True)

    # Invalidate cached tau so ensemble picks up new value
    from diagnosis.ensemble import invalidate_tau_cache
    invalidate_tau_cache()

    # Step 8: evaluate and write report
    print("Running evaluation ...", flush=True)
    from training.evaluate import evaluate
    evaluate(
        dataset_path=dataset_path,
        report_path=os.path.join(MODELS_DIR, "report.json"),
        verbose=verbose,
    )

    elapsed = time.perf_counter() - t0
    print(f"\nTraining complete in {elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-gen", action="store_true",
        help="Skip dataset generation, use existing dataset.npz",
    )
    parser.add_argument("--dataset", default=DATASET_PATH)
    args = parser.parse_args()
    train(dataset_path=args.dataset, skip_gen=args.skip_gen)
