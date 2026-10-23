"""
trust/calibration.py - Logistic calibration of P_correct from trust signals.

Fits a logistic model: P_correct = sigma(w dot [U_inv, S, N_inv, H, E, D])
where U_inv = 1-U and N_inv = 1-N (higher = more trustworthy).

If not enough data to fit, uses a simple weighted average of signals.
Loads/saves weights from models/calibration.json.
"""

from __future__ import annotations

import json
import os

import numpy as np

_CALIB_PATH = os.path.join(os.path.dirname(__file__), "../../models/calibration.json")

# Default weights for the linear combination (logistic regression)
# These are sensible priors before fitting; updated by make train.
# Input features: [1-U, S, 1-N, H, E, D]
_DEFAULT_WEIGHTS = [0.25, 0.15, 0.20, 0.15, 0.15, 0.10]  # sum = 1.0
_DEFAULT_BIAS    = 0.0


def _load_calibration() -> tuple[list[float], float]:
    """Load weights and bias from disk."""
    if os.path.exists(_CALIB_PATH):
        try:
            with open(_CALIB_PATH) as f:
                data = json.load(f)
            return data.get("weights", _DEFAULT_WEIGHTS), data.get("bias", _DEFAULT_BIAS)
        except Exception:
            pass
    return _DEFAULT_WEIGHTS, _DEFAULT_BIAS


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-float(x)))


def compute_p_correct(signals: dict[str, float]) -> float:
    """
    Compute P_correct from the six trust signals using the calibrated model.
    Returns a probability in [0, 1].

    Inputs expected: signals["U"], ["S"], ["N"], ["H"], ["E"], ["D"]
    """
    weights, bias = _load_calibration()

    # Build feature vector: [1-U, S, 1-N, H, E, D]
    U = float(signals.get("U", 0.5))
    S = float(signals.get("S", 0.5))
    N = float(signals.get("N", 0.5))
    H = float(signals.get("H", 0.5))
    E = float(signals.get("E", 0.5))
    D = float(signals.get("D", 0.5))

    features = [1.0 - U, S, 1.0 - N, H, E, D]
    w = weights[:len(features)]
    linear = sum(wi * fi for wi, fi in zip(w, features)) + bias

    # Scale to logit range so the output is sensitive to signal values
    # Multiply by 4 so full signal = logit 4 -> P~0.98, all-zero = logit -4 -> P~0.02
    p = _sigmoid(linear * 4.0 - 2.0)
    return float(np.clip(p, 0.01, 0.99))


def save_calibration(weights: list[float], bias: float) -> None:
    """Save calibration weights to disk."""
    os.makedirs(os.path.dirname(_CALIB_PATH) or ".", exist_ok=True)
    with open(_CALIB_PATH, "w") as f:
        json.dump({"weights": weights, "bias": bias}, f, indent=2)
