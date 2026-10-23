"""
diagnosis/mlp_mcd.py — MC-dropout MLP for multi-class diagnosis with uncertainty.

Architecture: 64 → 256 → 128 → 64 → 9 classes
Dropout(0.3) enabled at inference time for Monte Carlo uncertainty.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from sklearn.preprocessing import LabelEncoder

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "../../models/mlp_mcd.pt")

CLASS_ORDER = [
    "CPU_OVERLOAD", "MEMORY_EXHAUSTION", "MEMORY_LEAK",
    "TRAFFIC_OVERLOAD", "NETWORK_PARTITION", "BAD_DEPLOYMENT",
    "NODE_FAILURE", "NORMAL", "UNKNOWN",
]
N_CLASSES = len(CLASS_ORDER)


class _MLPNet(nn.Module):
    def __init__(self, dropout: float = 0.3) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(64, 256), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(256, 128), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(128, 64), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(64, N_CLASSES),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


@dataclass
class MCPrediction:
    mean_probs: np.ndarray    # shape (N_CLASSES,)
    std_probs: np.ndarray     # shape (N_CLASSES,)
    label: str
    confidence: float         # max mean prob
    uncertainty: float        # entropy of mean distribution


def _entropy(probs: np.ndarray) -> float:
    p = np.clip(probs, 1e-9, 1.0)
    return float(-np.sum(p * np.log(p)))


class MLPMCDClassifier:
    def __init__(self, dropout: float = 0.3) -> None:
        self._net = _MLPNet(dropout=dropout)
        self._fitted = False
        self._label_encoder = LabelEncoder()
        self._label_encoder.fit(CLASS_ORDER)

    def fit(self, X: np.ndarray, y: np.ndarray,
            epochs: int = 50, batch_size: int = 64, lr: float = 1e-3) -> None:
        """
        X: (N, 64) float32, y: (N,) string labels from CLASS_ORDER.
        Excludes UNKNOWN from training (UNKNOWN only appears via abstention).
        """
        # Filter out UNKNOWN labels
        mask = y != "UNKNOWN"
        X, y = X[mask], y[mask]

        y_enc = self._label_encoder.transform(y).astype(np.int64)
        X_t = torch.tensor(X, dtype=torch.float32)
        y_t = torch.tensor(y_enc, dtype=torch.long)

        dataset = TensorDataset(X_t, y_t)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
        optimiser = optim.Adam(self._net.parameters(), lr=lr, weight_decay=1e-4)
        criterion = nn.CrossEntropyLoss()
        scheduler = optim.lr_scheduler.StepLR(optimiser, step_size=20, gamma=0.5)

        self._net.train()
        for ep in range(epochs):
            for xb, yb in loader:
                optimiser.zero_grad()
                loss = criterion(self._net(xb), yb)
                loss.backward()
                optimiser.step()
            scheduler.step()

        self._fitted = True

    def predict_proba_mc(self, x: np.ndarray,
                         n_passes: int = 30) -> MCPrediction:
        """
        Run n_passes forward passes with dropout enabled.
        Returns mean and std of softmax probabilities.
        """
        if not self._fitted:
            uniform = np.ones(N_CLASSES) / N_CLASSES
            return MCPrediction(
                mean_probs=uniform, std_probs=np.zeros(N_CLASSES),
                label="UNKNOWN", confidence=1.0 / N_CLASSES, uncertainty=1.0,
            )

        x_t = torch.tensor(x if x.ndim == 2 else x.reshape(1, -1),
                            dtype=torch.float32)
        softmax = nn.Softmax(dim=-1)

        # Enable dropout at inference
        self._net.train()
        passes = []
        with torch.no_grad():
            for _ in range(n_passes):
                logits = self._net(x_t)
                probs = softmax(logits).squeeze(0).numpy()
                passes.append(probs)
        self._net.eval()

        stacked = np.stack(passes)             # (n_passes, N_CLASSES)
        mean_p  = stacked.mean(axis=0)         # (N_CLASSES,)
        std_p   = stacked.std(axis=0)          # (N_CLASSES,)

        label_idx = int(np.argmax(mean_p))
        label = CLASS_ORDER[label_idx]

        return MCPrediction(
            mean_probs=mean_p,
            std_probs=std_p,
            label=label,
            confidence=float(mean_p[label_idx]),
            uncertainty=_entropy(mean_p),
        )

    def save(self, path: str | None = None) -> None:
        path = path or _MODEL_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        torch.save({
            "state_dict": self._net.state_dict(),
            "fitted": self._fitted,
        }, path)

    def load(self, path: str | None = None) -> bool:
        path = path or _MODEL_PATH
        if not os.path.exists(path):
            return False
        data = torch.load(path, map_location="cpu", weights_only=False)
        self._net.load_state_dict(data["state_dict"])
        self._fitted = data["fitted"]
        self._net.eval()
        return True


_classifier: MLPMCDClassifier | None = None


def get_classifier() -> MLPMCDClassifier:
    global _classifier
    if _classifier is None:
        _classifier = MLPMCDClassifier()
        _classifier.load()
    return _classifier
