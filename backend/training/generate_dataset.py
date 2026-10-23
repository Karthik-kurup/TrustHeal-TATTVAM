"""
training/generate_dataset.py — Headless episode generation for training data.

Runs the twin without asyncio (pure synchronous step_physics calls).
Generates ≥30 labelled episodes per known fault class with intensity sweeps
and onset variations.

Output: a numpy .npz file at models/dataset.npz with:
  X       — (N, 64) float32 feature matrix
  y       — (N,) string label array
  fault   — (N,) string fault kind array (for debugging)
  episode — (N,) int episode index

Also writes models/dataset_info.json with episode statistics.

Usage:
  python -m training.generate_dataset          # default output
  python -m training.generate_dataset --out models/my_dataset.npz
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass

import numpy as np

# Allow running as __main__ from backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from twin.cluster import build_shop_cluster
from twin.workload import LoadGenerator, RngStreams
from twin.faults import (
    FaultSpec,
    FaultRegistry,
    FaultKind,
    OnsetShape,
    KNOWN_FAULT_KINDS,
    UNSEEN_FAULT_KINDS,
    FAULT_TRUTH_LABEL,
)
from twin.dynamics import step as dynamics_step
from twin.scheduler import tick_pending_pods, evict_unknown_pods
from twin.hpa import HpaController
from telemetry.store import TelemetryStore, TELEMETRY_INTERVAL_SIM, WINDOW_TICKS, BASELINE_TICKS
from telemetry.features import FeatureEngine, FEATURE_NAMES

logger = logging.getLogger(__name__)

MODELS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../models"))
DEFAULT_DATASET_PATH = os.path.join(MODELS_DIR, "dataset.npz")
DEFAULT_UNSEEN_DATASET_PATH = os.path.join(MODELS_DIR, "unseen_dataset.npz")

# ── Episode configuration ─────────────────────────────────────────────────────

EPISODES_PER_FAULT  = 30    # target episodes per known fault class
NORMAL_EPISODES     = 30    # episodes with no fault (label = NORMAL)
TELEMETRY_INTERVAL  = int(TELEMETRY_INTERVAL_SIM)  # 15 sim-s

# Burn-in time: enough to fill baseline (16 ticks) + partial window (5 ticks)
# baseline = 16 ticks × 15s = 240s; we start fault injection after baseline ready
BURNIN_TICKS = BASELINE_TICKS + 5                  # 21 ticks = 315 sim-s
BURNIN_SIM_SECONDS = BURNIN_TICKS * TELEMETRY_INTERVAL  # 315 sim-s

# How long to run fault episode to collect samples post-fault
FAULT_WINDOW_TICKS  = 20    # collect 20 ticks after fault fully ramped
FAULT_SIM_SECONDS   = 400   # 400 sim-s of fault-active window

# Total sim-s per episode = burnin + fault window
TOTAL_SIM_SECONDS   = BURNIN_SIM_SECONDS + FAULT_SIM_SECONDS  # 715 sim-s

# Known services to sample from (one episode = one fault on one service)
KNOWN_SERVICES = ["frontend", "catalog", "cart", "checkout", "payment", "cache"]
# node targets for node_failure
KNOWN_NODES = ["node-1", "node-2", "node-3", "node-4"]

# Intensity sweep values
INTENSITIES   = [0.5, 0.7, 0.9, 1.0]
# Onset shape sweep
ONSET_SHAPES  = [OnsetShape.step, OnsetShape.sigmoid]
UNSEEN_EPISODES_PER_FAULT = 5


@dataclass
class EpisodeConfig:
    fault_kind: FaultKind
    target: str
    intensity: float
    onset_shape: OnsetShape
    seed: int
    is_normal: bool = False


def _make_episode_configs(seed_base: int = 1000) -> list[EpisodeConfig]:
    """Generate episode configs sweeping intensity, onset, and seed for each known fault."""
    configs: list[EpisodeConfig] = []
    ep_seed = seed_base

    # Known faults
    for fault_kind in sorted(KNOWN_FAULT_KINDS, key=lambda k: k.value):
        # Choose appropriate targets
        if fault_kind == FaultKind.node_failure:
            targets = KNOWN_NODES
        else:
            targets = KNOWN_SERVICES

        ep_per_fault = 0
        # Cycle through intensities × onset shapes × targets until we have enough
        combos = [(t, i, o) for t in targets for i in INTENSITIES for o in ONSET_SHAPES]
        idx = 0
        while ep_per_fault < EPISODES_PER_FAULT:
            target, intensity, onset = combos[idx % len(combos)]
            configs.append(EpisodeConfig(
                fault_kind=fault_kind,
                target=target,
                intensity=intensity,
                onset_shape=onset,
                seed=ep_seed,
            ))
            ep_per_fault += 1
            ep_seed += 1
            idx += 1

    # Normal episodes (no fault)
    for _ in range(NORMAL_EPISODES):
        configs.append(EpisodeConfig(
            fault_kind=FaultKind.cpu_stress,  # placeholder; will be skipped
            target="none",
            intensity=0.0,
            onset_shape=OnsetShape.step,
            seed=ep_seed,
            is_normal=True,
        ))
        ep_seed += 1

    return configs


def _make_unseen_episode_configs(seed_base: int = 50000) -> list[EpisodeConfig]:
    configs: list[EpisodeConfig] = []
    episode = 0
    for fault_kind in sorted(UNSEEN_FAULT_KINDS, key=lambda kind: kind.value):
        for index in range(UNSEEN_EPISODES_PER_FAULT):
            configs.append(EpisodeConfig(
                fault_kind=fault_kind,
                target=KNOWN_SERVICES[index % len(KNOWN_SERVICES)],
                intensity=(0.7, 1.0, 1.2)[index % 3],
                onset_shape=ONSET_SHAPES[index % len(ONSET_SHAPES)],
                seed=seed_base + episode,
            ))
            episode += 1
    return configs


def _run_episode(
    config: EpisodeConfig,
) -> tuple[list[np.ndarray], list[str], list[str], list[str]]:
    """
    Run one headless episode synchronously.
    Returns (feature_rows, labels, fault_kinds).
    Rows collected only during the fault-active window (or all window for normal).
    """
    rng = RngStreams.from_seed(config.seed)
    cluster = build_shop_cluster()
    faults  = FaultRegistry()
    load_gen = LoadGenerator(rng=rng)
    hpa      = HpaController()
    telemetry = TelemetryStore()
    fe        = FeatureEngine()
    sim_time  = 0.0

    fault_injected_at: float | None = None
    fault_id: str | None = None
    truth_label = "NORMAL" if config.is_normal else FAULT_TRUTH_LABEL[config.fault_kind]

    features_list: list[np.ndarray] = []
    labels_list:   list[str]        = []
    fault_kinds:   list[str]        = []
    service_ids:   list[str]        = []

    def _tick() -> None:
        nonlocal sim_time
        dynamics_step(cluster, load_gen, faults)
        tick_pending_pods(cluster, cluster.sim_time)
        evict_unknown_pods(cluster, cluster.sim_time)

        if int(cluster.sim_time) % TELEMETRY_INTERVAL == 0:
            _sample_telemetry()

    def _sample_telemetry() -> None:
        events = cluster.drain_events()
        from twin.cluster import ClusterSnapshot
        snap = ClusterSnapshot(
            sim_time=cluster.sim_time,
            nodes=list(cluster.nodes.values()),
            pods=list(cluster.pods.values()),
            services=list(cluster.services.values()),
            events=events,
        )
        telemetry.record(snap)
        hpa.tick(cluster, cluster.sim_time)

        # Feature extraction per service
        for svc_id in telemetry.services():
            fv = fe.compute(svc_id, telemetry)
            if fv is None:
                continue

            # Only collect samples during the fault-active window
            in_fault_window = (
                fault_injected_at is not None
                and cluster.sim_time >= fault_injected_at + 30  # after onset ramp
            )
            is_normal_episode = config.is_normal

            if in_fault_window or is_normal_episode:
                # Limit samples per episode to FAULT_WINDOW_TICKS
                if len(features_list) < FAULT_WINDOW_TICKS * len(KNOWN_SERVICES):
                    features_list.append(fv.to_numpy())
                    labels_list.append(truth_label)
                    fault_kinds.append("none" if config.is_normal else config.fault_kind.value)
                    service_ids.append(svc_id)

    # ── Burn-in phase ─────────────────────────────────────────────────────────
    for _ in range(BURNIN_SIM_SECONDS):
        _tick()

    # ── Inject fault ──────────────────────────────────────────────────────────
    if not config.is_normal:
        spec = FaultSpec(
            kind=config.fault_kind,
            target=config.target,
            intensity=config.intensity,
            onset_shape=config.onset_shape,
            onset_duration=30.0,
            duration=FAULT_SIM_SECONDS,
        )
        fault_injected_at = cluster.sim_time
        fault_id = faults.inject(spec, fault_injected_at)

    # ── Fault-active window ───────────────────────────────────────────────────
    for _ in range(FAULT_SIM_SECONDS):
        _tick()
        faults.tick(cluster.sim_time)

    return features_list, labels_list, fault_kinds, service_ids


def _generate_from_configs(
    configs: list[EpisodeConfig],
    out_path: str,
    verbose: bool,
) -> tuple[np.ndarray, np.ndarray]:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    total = len(configs)

    all_X:       list[np.ndarray] = []
    all_y:       list[str]        = []
    all_faults:  list[str]        = []
    all_services: list[str]       = []
    all_episodes: list[int]       = []

    t0 = time.perf_counter()
    for ep_idx, config in enumerate(configs):
        if verbose:
            label_name = "NORMAL" if config.is_normal else FAULT_TRUTH_LABEL[config.fault_kind]
            print(f"  Episode {ep_idx+1:3d}/{total} — {label_name:<20s} "
                  f"target={config.target:<12s} intensity={config.intensity:.1f} "
                  f"seed={config.seed}", end="", flush=True)

        rows, labels, kinds, services = _run_episode(config)
        n = len(rows)

        if verbose:
            print(f"  -> {n} samples", flush=True)

        if n > 0:
            all_X.extend(rows)
            all_y.extend(labels)
            all_faults.extend(kinds)
            all_services.extend(services)
            all_episodes.extend([ep_idx] * n)

    elapsed = time.perf_counter() - t0

    X = np.array(all_X, dtype=np.float32)
    y = np.array(all_y, dtype=str)
    faults_arr = np.array(all_faults, dtype=str)
    services_arr = np.array(all_services, dtype=str)
    episodes_arr = np.array(all_episodes, dtype=np.int32)

    np.savez(out_path.replace(".npz", ""), X=X, y=y,
             fault=faults_arr, episode=episodes_arr,
             service=services_arr,
             feature_names=np.array(FEATURE_NAMES))

    # Statistics
    unique, counts = np.unique(y, return_counts=True)
    stats = {
        "total_samples": int(len(X)),
        "total_episodes": int(total),
        "elapsed_seconds": round(elapsed, 1),
        "class_counts": {str(k): int(v) for k, v in zip(unique, counts)},
        "feature_names": FEATURE_NAMES,
    }
    info_path = out_path.replace(".npz", "_info.json")
    with open(info_path, "w") as f:
        json.dump(stats, f, indent=2)

    if verbose:
        print(f"\nDataset: {len(X)} samples from {total} episodes in {elapsed:.1f}s")
        for cls, cnt in zip(unique, counts):
            print(f"  {cls:<20s}: {cnt:5d}")

    return X, y


def generate(
    out_path: str = DEFAULT_DATASET_PATH,
    seed_base: int = 1000,
    verbose: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate known-fault and normal training samples."""
    return _generate_from_configs(
        _make_episode_configs(seed_base=seed_base),
        out_path=out_path,
        verbose=verbose,
    )


def generate_unseen(
    out_path: str = DEFAULT_UNSEEN_DATASET_PATH,
    seed_base: int = 50000,
    verbose: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate a separate, UNKNOWN-labeled evaluation set excluded from training."""
    return _generate_from_configs(
        _make_unseen_episode_configs(seed_base=seed_base),
        out_path=out_path,
        verbose=verbose,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--unseen", action="store_true")
    args = parser.parse_args()
    out_path = args.out or (
        DEFAULT_UNSEEN_DATASET_PATH if args.unseen else DEFAULT_DATASET_PATH
    )
    generator = generate_unseen if args.unseen else generate
    seed_base = args.seed if args.seed is not None else (50000 if args.unseen else 1000)
    generator(out_path=out_path, seed_base=seed_base)
