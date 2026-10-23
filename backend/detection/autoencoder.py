"""
detection/autoencoder.py — Small PyTorch autoencoder for anomaly detection.

Architecture: 64 → 32 → 16 → 32 → 64
Trained on NORMAL windows only.
Anomaly score = reconstruction error normalised by 99th percentile of training errors.
"""

from __future__ import annotations

import os
import time

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "../../models/autoencoder.pt")


class _AENet(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(64, 32), nn.ReLU(),
            nn.Linear(32, 16), nn.ReLU(),
        )
        self.decoder = nn.Sequential(
            nn.Linear(16, 32), nn.ReLU(),
            nn.Linear(32, 64),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.decoder(self.encoder(x))


class AutoencoderDetector:
    def __init__(self) -> None:
        self._net = _AENet()
        self._fitted = False
        self._p99_error: float = 1.0  # normalisation denominator

    def fit(self, X: np.ndarray,
            epochs: int = 30, batch_size: int = 64, lr: float = 1e-3) -> None:
        """Train on NORMAL windows only."""
        X_t = torch.tensor(X, dtype=torch.float32)
        dataset = TensorDataset(X_t)
        loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
        optimiser = optim.Adam(self._net.parameters(), lr=lr)
        criterion = nn.MSELoss()

        self._net.train()
        for _ in range(epochs):
            for (batch,) in loader:
                optimiser.zero_grad()
                out = self._net(batch)
                loss = criterion(out, batch)
                loss.backward()
                optimiser.step()

        # Compute per-sample reconstruction errors for normalisation
        self._net.eval()
        with torch.no_grad():
            recon = self._net(X_t)
            errors = ((recon - X_t) ** 2).mean(dim=1).numpy()
        self._p99_error = float(np.percentile(errors, 99))
        if self._p99_error < 1e-9:
            self._p99_error = 1.0
        self._fitted = True

    def score(self, x: np.ndarray) -> float:
        """Return anomaly score [0, 1]."""
        if not self._fitted:
            return 0.0
        x_t = torch.tensor(x if x.ndim == 2 else x.reshape(1, -1),
                            dtype=torch.float32)
        self._net.eval()
        with torch.no_grad():
            recon = self._net(x_t)
            err = float(((recon - x_t) ** 2).mean().item())
        return float(np.clip(err / self._p99_error, 0.0, 1.0))

    def save(self, path: str | None = None) -> None:
        path = path or _MODEL_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        torch.save({
            "state_dict": self._net.state_dict(),
            "p99_error": self._p99_error,
            "fitted": self._fitted,
        }, path)

    def load(self, path: str | None = None) -> bool:
        path = path or _MODEL_PATH
        if not os.path.exists(path):
            return False
        data = torch.load(path, map_location="cpu", weights_only=False)
        self._net.load_state_dict(data["state_dict"])
        self._p99_error = data["p99_error"]
        self._fitted = data["fitted"]
        self._net.eval()
        return True


_detector: AutoencoderDetector | None = None


def get_detector() -> AutoencoderDetector:
    global _detector
    if _detector is None:
        _detector = AutoencoderDetector()
        _detector.load()
    return _detector
