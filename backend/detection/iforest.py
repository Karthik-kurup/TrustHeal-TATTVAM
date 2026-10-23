"""
detection/iforest.py — Isolation Forest anomaly detector.

Trained on NORMAL feature windows only.
score() → float [0, 1] where 1 = most anomalous.
"""

from __future__ import annotations

import os
import warnings

import numpy as np
from sklearn.ensemble import IsolationForest
import joblib

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "../../models/iforest.joblib")


class IForestDetector:
    def __init__(self, n_estimators: int = 100, contamination: float = 0.05) -> None:
        self._model = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,
            random_state=42,
            n_jobs=-1,
        )
        self._fitted = False
        # Score normalisation: raw scores shifted so 0=normal, 1=anomaly
        self._score_min: float = -0.5
        self._score_max: float = 0.0

    def fit(self, X: np.ndarray) -> None:
        """Fit on NORMAL windows only."""
        self._model.fit(X)
        # Calibrate normalisation using the training data
        raw = self._model.score_samples(X)  # higher = more normal (negative range)
        self._score_min = float(np.percentile(raw, 1))
        self._score_max = float(np.percentile(raw, 99))
        self._fitted = True

    def score(self, x: np.ndarray) -> float:
        """
        Return anomaly score [0, 1].
        x: shape (64,) or (1, 64).
        """
        if not self._fitted:
            return 0.0
        if x.ndim == 1:
            x = x.reshape(1, -1)
        raw = float(self._model.score_samples(x)[0])
        # Invert: lower raw score = more anomalous → higher output score
        span = self._score_max - self._score_min
        if span < 1e-9:
            return 0.0
        normalised = (raw - self._score_min) / span
        return float(np.clip(1.0 - normalised, 0.0, 1.0))

    def save(self, path: str | None = None) -> None:
        path = path or _MODEL_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        joblib.dump({"model": self._model,
                     "score_min": self._score_min,
                     "score_max": self._score_max,
                     "fitted": self._fitted}, path)

    def load(self, path: str | None = None) -> bool:
        path = path or _MODEL_PATH
        if not os.path.exists(path):
            return False
        data = joblib.load(path)
        self._model = data["model"]
        self._score_min = data["score_min"]
        self._score_max = data["score_max"]
        self._fitted = data["fitted"]
        return True


# Module-level singleton loaded at import time (after training)
_detector: IForestDetector | None = None


def get_detector() -> IForestDetector:
    global _detector
    if _detector is None:
        _detector = IForestDetector()
        _detector.load()
    return _detector
