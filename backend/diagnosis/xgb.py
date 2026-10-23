"""
diagnosis/xgb.py — XGBoost multi-class classifier with SHAP attribution.

Falls back to feature_importances_ if SHAP takes > 200 ms.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import numpy as np
import xgboost as xgb

from diagnosis.mlp_mcd import CLASS_ORDER, N_CLASSES

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "../../models/xgb.json")


class XGBClassifier:
    def __init__(self) -> None:
        self._model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.1,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="mlogloss",
            random_state=42,
            n_jobs=-1,
        )
        self._fitted = False
        self._feature_names: list[str] = []
        self._classes: list[str] = CLASS_ORDER[:]
        self._explainer: Any | None = None
        self._explain_lock = threading.Lock()

    def fit(self, X: np.ndarray, y: np.ndarray,
            feature_names: list[str] | None = None) -> None:
        """y: string labels."""
        from sklearn.preprocessing import LabelEncoder
        # Filter UNKNOWN from training
        mask = y != "UNKNOWN"
        X, y = X[mask], y[mask]

        le = _get_label_encoder()
        y_enc = le.transform(y).astype(np.int32)
        self._feature_names = feature_names or [f"f{i}" for i in range(X.shape[1])]
        self._model.fit(X, y_enc)
        self._fitted = True
        self._explainer = None

    def predict_proba(self, x: np.ndarray) -> dict[str, float]:
        """Return dict mapping class label → probability."""
        if not self._fitted:
            return {c: 1.0 / N_CLASSES for c in self._classes}
        if x.ndim == 1:
            x = x.reshape(1, -1)
        probs = self._model.predict_proba(x)[0]
        # XGBoost returns probs in label-encoder order (alphabetical), not CLASS_ORDER
        le = _get_label_encoder()
        # Map: le.classes_[i] -> probs[i]
        label_to_prob = {str(cls): float(p) for cls, p in zip(le.classes_, probs)}
        # Return in CLASS_ORDER, 0 for UNKNOWN (never trained)
        return {c: label_to_prob.get(c, 0.0) for c in CLASS_ORDER}

    def explain(self, x: np.ndarray, timeout_ms: float = 200.0
                ) -> dict[str, float]:
        """
        Return feature attribution dict {feature_name: importance}.
        Uses SHAP if fast enough, else falls back to XGBoost built-in importances.
        """
        if not self._fitted:
            return {}
        if x.ndim == 1:
            x = x.reshape(1, -1)

        t0 = time.perf_counter()
        try:
            import shap
            setup_ms = 0.0
            with self._explain_lock:
                if self._explainer is None:
                    setup_start = time.perf_counter()
                    self._explainer = shap.TreeExplainer(self._model)
                    setup_ms = (time.perf_counter() - setup_start) * 1000
                explainer = self._explainer
                shap_values = explainer.shap_values(x)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if setup_ms > timeout_ms * 0.5:
                raise RuntimeError("SHAP explanation too slow — using fallback")
            if elapsed_ms > timeout_ms:
                raise RuntimeError("SHAP too slow — using fallback")
            # shap_values: list of arrays (one per class) or 3-D array
            if isinstance(shap_values, list):
                # Mean absolute SHAP across classes
                combined = np.mean([np.abs(sv) for sv in shap_values], axis=0)
            else:
                combined = np.abs(shap_values).mean(axis=(0, 1)) if shap_values.ndim == 3 \
                           else np.abs(shap_values[0])
            names = self._feature_names or [f"f{i}" for i in range(combined.shape[0])]
            return {n: float(v) for n, v in zip(names, combined.flatten())}
        except Exception:
            # Fallback: XGBoost built-in feature importance
            imp = self._model.feature_importances_
            names = self._feature_names or [f"f{i}" for i in range(len(imp))]
            return {n: float(v) for n, v in zip(names, imp)}

    def save(self, path: str | None = None) -> None:
        path = path or _MODEL_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._model.save_model(path)
        # Save metadata alongside
        import json
        meta_path = path.replace(".json", "_meta.json")
        with open(meta_path, "w") as f:
            json.dump({"feature_names": self._feature_names,
                       "fitted": self._fitted}, f)

    def load(self, path: str | None = None) -> bool:
        path = path or _MODEL_PATH
        if not os.path.exists(path):
            return False
        self._model.load_model(path)
        self._fitted = True
        self._explainer = None
        import json
        meta_path = path.replace(".json", "_meta.json")
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            self._feature_names = meta.get("feature_names", [])
        return True


def _get_label_encoder():
    from sklearn.preprocessing import LabelEncoder
    le = LabelEncoder()
    le.fit([c for c in CLASS_ORDER if c != "UNKNOWN"])
    return le


_classifier: XGBClassifier | None = None


def get_classifier() -> XGBClassifier:
    global _classifier
    if _classifier is None:
        _classifier = XGBClassifier()
        _classifier.load()
    return _classifier
