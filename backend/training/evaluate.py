"""
training/evaluate.py - Evaluate all trained models and write models/report.json.

Computes:
  - Confusion matrix for the ensemble (on held-out last 20% of episodes)
  - Per-class precision, recall, F1
  - Detector comparison (rules, iforest, autoencoder AUROC vs each other)
  - Unseen-fault escalation rate (target: 0 wrongly-AUTO per spec sec14)
  - Calibration metrics (reliability diagram data)

Writes:
  models/report.json   -- consumed by the Models and Trust frontend page

Usage:
  python -m training.evaluate
  python -m training.evaluate --dataset models/dataset.npz --report models/report.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

import numpy as np
from sklearn.metrics import (
    confusion_matrix, classification_report,
    roc_auc_score,
)

logger = logging.getLogger(__name__)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

MODELS_DIR   = os.path.join(os.path.dirname(__file__), "../../models")
DATASET_PATH = os.path.join(MODELS_DIR, "dataset.npz")
UNSEEN_DATASET_PATH = os.path.join(MODELS_DIR, "unseen_dataset.npz")
REPORT_PATH  = os.path.join(MODELS_DIR, "report.json")


def _load_dataset(path: str):
    data = np.load(path, allow_pickle=True)
    X        = data["X"].astype(np.float32)
    y        = data["y"].astype(str)
    episodes = data["episode"].astype(np.int32)
    faults   = data["fault"].astype(str)
    return X, y, episodes, faults


def _held_out_split(X, y, episodes, faults, held_frac: float = 0.2):
    """Hold out held_frac of episodes per class for evaluation (stratified)."""
    # Build per-class episode lists and hold out the last held_frac of each
    rng = np.random.RandomState(0)
    held_eps: set = set()
    # Map episode -> majority class
    ep_classes: dict[int, str] = {}
    for ep, lbl in zip(episodes, y):
        ep_classes[int(ep)] = lbl  # last label wins (all same in an episode)
    # Group episodes by class
    class_eps: dict[str, list] = {}
    for ep, cls in ep_classes.items():
        class_eps.setdefault(cls, []).append(ep)
    for cls, eps in class_eps.items():
        eps_arr = np.array(sorted(eps))
        rng.shuffle(eps_arr)
        n_held = max(1, int(len(eps_arr) * held_frac))
        held_eps.update(eps_arr[-n_held:].tolist())
    test_mask  = np.array([ep in held_eps for ep in episodes])
    train_mask = ~test_mask
    return (X[train_mask], y[train_mask], episodes[train_mask], faults[train_mask],
            X[test_mask],  y[test_mask],  episodes[test_mask],  faults[test_mask])


def _ensemble_predict_batch(X: np.ndarray, y_true: np.ndarray):
    """
    Run ensemble on a batch of feature vectors.
    Returns (y_pred, y_conf, abstained_mask).
    Loads trained models from disk.
    """
    from diagnosis.mlp_mcd import MLPMCDClassifier, CLASS_ORDER
    from diagnosis.xgb import XGBClassifier
    import json as _json

    # Load MLP
    mlp = MLPMCDClassifier()
    ok = mlp.load()
    if not ok:
        logger.warning("MLP model not found -- evaluation will be uniform")

    # Load XGB
    xgb = XGBClassifier()
    xgb.load()

    # Load tau
    thresholds_path = os.path.join(MODELS_DIR, "thresholds.json")
    tau = 0.55
    if os.path.exists(thresholds_path):
        with open(thresholds_path) as f:
            tau = _json.load(f).get("tau_abstain", 0.55)

    # Weights
    W_MLP, W_XGB = 0.45, 0.45

    y_pred: list[str] = []
    y_conf: list[float] = []
    abstained: list[bool] = []

    for xi in X:
        mc = mlp.predict_proba_mc(xi, n_passes=10)
        mlp_vec = mc.mean_probs

        xgb_d = xgb.predict_proba(xi)
        xgb_vec = np.array([xgb_d.get(c, 0.0) for c in CLASS_ORDER])

        # No evidence rules here (needs FeatureVector) -- use MLP+XGB only
        ens_vec = (W_MLP * mlp_vec + W_XGB * xgb_vec)
        total = ens_vec.sum()
        if total > 0:
            ens_vec = ens_vec / total

        best_idx   = int(np.argmax(ens_vec))
        best_label = CLASS_ORDER[best_idx]
        best_conf  = float(ens_vec[best_idx])

        ab = best_conf < tau or best_label == "UNKNOWN"
        if ab:
            best_label = "UNKNOWN"

        y_pred.append(best_label)
        y_conf.append(best_conf)
        abstained.append(ab)

    return np.array(y_pred), np.array(y_conf), np.array(abstained)


def _compute_detector_scores(X_normal: np.ndarray,
                              X_fault:  np.ndarray) -> dict:
    """Compute AUROC for each detector (normal=0, fault=1)."""
    from detection.iforest import IForestDetector
    from detection.autoencoder import AutoencoderDetector
    import detection.rules as rules_mod
    from telemetry.features import FEATURE_NAMES, FeatureVector

    results: dict = {}
    n_normal = len(X_normal)
    n_fault  = len(X_fault)
    X_all   = np.vstack([X_normal, X_fault])
    y_bin   = np.array([0] * n_normal + [1] * n_fault)

    # IsolationForest
    try:
        iforest = IForestDetector()
        ok = iforest.load()
        if ok:
            scores = np.array([iforest.score(xi) for xi in X_all])
            auroc = float(roc_auc_score(y_bin, scores))
            results["iforest"] = {"auroc": auroc}
    except Exception as e:
        results["iforest"] = {"error": str(e)}

    # Autoencoder
    try:
        ae = AutoencoderDetector()
        ok = ae.load()
        if ok:
            scores = np.array([ae.score(xi) for xi in X_all])
            auroc = float(roc_auc_score(y_bin, scores))
            results["autoencoder"] = {"auroc": auroc}
    except Exception as e:
        results["autoencoder"] = {"error": str(e)}

    # Rules detector (module-level score function)
    try:
        scores = []
        for xi in X_all:
            fv = FeatureVector(
                sim_time=0.0, service_id="eval",
                features=xi.tolist(), feature_names=FEATURE_NAMES,
                window_start=0.0, window_end=0.0,
            )
            scores.append(rules_mod.score(fv))
        scores_arr = np.array(scores)
        auroc = float(roc_auc_score(y_bin, scores_arr))
        results["rules"] = {"auroc": auroc}
    except Exception as e:
        results["rules"] = {"error": str(e)}

    return results


def _calibration_data(y_true: np.ndarray, y_conf: np.ndarray,
                       y_pred: np.ndarray, n_bins: int = 10) -> list[dict]:
    """
    Compute reliability diagram data: for each confidence bin,
    the mean confidence and the fraction of correct predictions.
    """
    correct = (y_true == y_pred).astype(float)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    result = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (y_conf >= lo) & (y_conf < hi)
        if mask.sum() == 0:
            continue
        result.append({
            "bin_lo": round(float(lo), 2),
            "bin_hi": round(float(hi), 2),
            "mean_conf": round(float(y_conf[mask].mean()), 3),
            "frac_correct": round(float(correct[mask].mean()), 3),
            "count": int(mask.sum()),
        })
    return result


def _wilson_interval(successes: int, total: int) -> tuple[float | None, float | None]:
    if total == 0:
        return None, None
    z = 1.96
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * ((proportion * (1 - proportion) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _evaluate_unseen_gate(
    X: np.ndarray,
    fault_kinds: np.ndarray,
    service_ids: np.ndarray,
    limit_per_fault: int = 5,
) -> dict:
    """Evaluate the real diagnosis and trust-gate path on a balanced unseen sample."""
    from diagnosis.ensemble import diagnose
    from healing.runbook import next_action
    from telemetry.features import FEATURE_NAMES, FeatureVector
    from telemetry.health import HealthStatus
    from trust.gate import evaluate as evaluate_gate

    selected: list[int] = []
    for kind in sorted(set(fault_kinds.tolist())):
        indices = np.flatnonzero(fault_kinds == kind)
        count = min(limit_per_fault, len(indices))
        if count:
            selected.extend(indices[np.linspace(0, len(indices) - 1, count, dtype=int)].tolist())

    escalated = 0
    wrongly_auto = 0
    asked = 0
    per_fault: dict[str, dict[str, int]] = {}
    for index in selected:
        fv = FeatureVector(
            sim_time=float(index),
            service_id=str(service_ids[index]),
            features=X[index].tolist(),
            feature_names=FEATURE_NAMES,
            window_start=0.0,
            window_end=0.0,
        )
        result = diagnose(fv)
        step = next_action(result.label, fv.service_id, 0)
        gate_result = evaluate_gate(
            fv=fv,
            ensemble=result,
            health_status=HealthStatus.BAD,
            proposed_action=step.action,
            proposed_target=step.target,
            autonomy_stage="S3",
        )
        kind = str(fault_kinds[index])
        metrics = per_fault.setdefault(
            kind,
            {"total": 0, "escalated": 0, "asked": 0, "wrongly_auto": 0},
        )
        metrics["total"] += 1
        if gate_result.decision.value == "ESCALATE":
            escalated += 1
            metrics["escalated"] += 1
        elif gate_result.decision.value == "AUTO":
            wrongly_auto += 1
            metrics["wrongly_auto"] += 1
        else:
            asked += 1
            metrics["asked"] += 1

    ci_lower, ci_upper = _wilson_interval(wrongly_auto, len(selected))
    return {
        "total": len(selected),
        "escalated": escalated,
        "asked": asked,
        "wrongly_auto": wrongly_auto,
        "wrongly_auto_rate": wrongly_auto / len(selected) if selected else None,
        "wilson_ci_lower": ci_lower,
        "wilson_ci_upper": ci_upper,
        "fault_kinds": sorted(set(fault_kinds.tolist())),
        "per_fault": per_fault,
        "evaluation_method": "trust_gate",
    }


def evaluate(dataset_path: str = DATASET_PATH,
             report_path: str  = REPORT_PATH,
             unseen_dataset_path: str = UNSEEN_DATASET_PATH,
             verbose: bool     = True) -> dict:
    """Main evaluation entry point. Returns the report dict."""
    os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
    t0 = time.perf_counter()

    if not os.path.exists(dataset_path):
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    X, y, episodes, faults = _load_dataset(dataset_path)

    # Split into train/test by episode
    _, _, _, _, X_test, y_test, _, faults_test = _held_out_split(X, y, episodes, faults)

    known_mask   = np.array([lbl != "UNKNOWN" for lbl in y_test])
    unknown_mask = ~known_mask
    X_known,  y_known  = X_test[known_mask],  y_test[known_mask]
    X_unseen, y_unseen = X_test[unknown_mask], y_test[unknown_mask]
    faults_unseen = faults_test[unknown_mask]
    service_unseen = np.full(len(X_unseen), "eval", dtype=str)

    if os.path.exists(unseen_dataset_path):
        X_extra, y_extra, _, faults_extra = _load_dataset(unseen_dataset_path)
        unseen_mask = y_extra == "UNKNOWN"
        X_unseen = X_extra[unseen_mask]
        y_unseen = y_extra[unseen_mask]
        faults_unseen = faults_extra[unseen_mask]
        with np.load(unseen_dataset_path, allow_pickle=True) as unseen_data:
            if "service" in unseen_data.files:
                service_unseen = unseen_data["service"].astype(str)[unseen_mask]
            else:
                service_unseen = np.full(len(X_unseen), "eval", dtype=str)

    if verbose:
        print(f"  Held-out: {len(X_test)} samples "
              f"({len(X_known)} known, {len(X_unseen)} unseen evaluation samples)", flush=True)

    # Confusion matrix on known faults
    if len(X_known) > 0:
        y_pred_known, y_conf_known, abs_known = _ensemble_predict_batch(X_known, y_known)
        from diagnosis.mlp_mcd import CLASS_ORDER
        labels = [c for c in CLASS_ORDER if c != "UNKNOWN"] + ["UNKNOWN"]
        cm = confusion_matrix(y_known, y_pred_known, labels=labels)
        cr = classification_report(y_known, y_pred_known,
                                   labels=labels, output_dict=True,
                                   zero_division=0)
        calibration = _calibration_data(y_known, y_conf_known, y_pred_known)
        abstain_rate = float(abs_known.mean())
    else:
        cm = np.zeros((9, 9), dtype=int)
        cr = {}
        calibration = []
        abstain_rate = 0.0
        labels = []
        y_pred_known = np.array([])
        y_conf_known = np.array([])

    # Unseen-fault evaluation
    # All unseen faults must be UNKNOWN (escalate); none must be AUTO-acted on.
    unseen_stats: dict = {
        "total": 0,
        "escalated": 0,
        "asked": 0,
        "wrongly_auto": 0,
        "wrongly_auto_rate": None,
        "wilson_ci_lower": None,
        "wilson_ci_upper": None,
        "fault_kinds": [],
        "evaluation_method": "trust_gate",
    }
    if len(X_unseen) > 0:
        unseen_stats = _evaluate_unseen_gate(
            X_unseen, faults_unseen, service_unseen
        )
        if verbose:
            print(
                f"  Unseen gate: {unseen_stats['wrongly_auto']}/"
                f"{unseen_stats['total']} AUTO actions (target: 0)",
                flush=True,
            )

    # Detector comparison
    normal_mask_all = y == "NORMAL"
    fault_mask_all  = y != "NORMAL"
    X_normal_all = X[normal_mask_all]
    X_fault_all  = X[fault_mask_all]
    # Use subset to keep evaluation fast
    n_det = min(200, len(X_normal_all), len(X_fault_all))
    rng = np.random.RandomState(0)
    ni = rng.choice(len(X_normal_all), n_det, replace=False)
    fi = rng.choice(len(X_fault_all),  n_det, replace=False)
    detector_results = _compute_detector_scores(X_normal_all[ni], X_fault_all[fi])

    # Assemble report
    elapsed = time.perf_counter() - t0
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": round(elapsed, 1),
        "n_test_samples": int(len(X_test)),
        "n_known_samples": int(len(X_known)),
        "n_unseen_samples": int(len(X_unseen)),
        "class_labels": labels,
        "confusion_matrix": cm.tolist(),
        "classification_report": cr,
        "abstain_rate": round(abstain_rate, 3),
        "calibration": calibration,
        "unseen_faults": unseen_stats,
        "detectors": detector_results,
    }

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    if verbose:
        print(f"  Report written -> {report_path}", flush=True)
        if cr:
            wa = cr.get("weighted avg", {})
            print(f"  Weighted avg -- precision={wa.get('precision',0):.3f}  "
                  f"recall={wa.get('recall',0):.3f}  "
                  f"f1={wa.get('f1-score',0):.3f}", flush=True)

    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=DATASET_PATH)
    parser.add_argument("--report",  default=REPORT_PATH)
    parser.add_argument("--unseen-dataset", default=UNSEEN_DATASET_PATH)
    args = parser.parse_args()
    evaluate(
        dataset_path=args.dataset,
        report_path=args.report,
        unseen_dataset_path=args.unseen_dataset,
    )
