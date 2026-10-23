"""
twin/workload.py — Load generator with daily traffic pattern, noise and benign bursts.

Uses three separate seeded RNG streams (workload, fault_schedule, dynamics_noise)
so that Compare Lab arms cannot perturb each other's random sequences.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from twin.cluster import ClusterState


# ── RNG streams ───────────────────────────────────────────────────────────────

@dataclass
class RngStreams:
    """
    Three independent seeded RNG streams per simulation arm.
    Keeping them separate means an action in arm A (e.g. scale-up that
    changes how many pods consume noise) cannot shift the noise sequence
    seen by arm B.
    """
    workload: random.Random        # traffic pattern + benign bursts
    fault_schedule: random.Random  # chaos-mode fault injection timing
    dynamics_noise: random.Random  # per-tick physics jitter

    @classmethod
    def from_seed(cls, seed: int) -> "RngStreams":
        return cls(
            workload=random.Random(seed),
            fault_schedule=random.Random(seed + 1_000_000),
            dynamics_noise=random.Random(seed + 2_000_000),
        )


# ── Daily traffic pattern ─────────────────────────────────────────────────────

# Peak hours: ramp up from 08:00 (28800s), peak at 13:00, fall off by 22:00.
# Simulated day = 86400 sim-seconds.

def _daily_multiplier(sim_time: float) -> float:
    """
    Return a traffic multiplier [0.2, 1.0] based on time-of-day.
    sim_time is in simulated seconds; one sim-day = 86400 s.
    """
    day_seconds = 86400.0
    t = (sim_time % day_seconds) / day_seconds  # 0‥1
    # Smooth double-peak (morning and evening) using cosine
    # Minimum 0.2 (night), maximum 1.0 (lunch/evening peaks)
    base = 0.5 - 0.3 * math.cos(2 * math.pi * t)
    # Extra lunch dip at t=0.55 (13:00)
    lunch_dip = -0.1 * math.exp(-((t - 0.55) ** 2) / 0.005)
    return max(0.15, min(1.0, base + lunch_dip))


# ── Per-service baseline request rates (req/s at peak) ───────────────────────

SERVICE_BASELINE_RPS: dict[str, float] = {
    "frontend":  120.0,
    "catalog":   90.0,
    "cart":      60.0,
    "checkout":  45.0,
    "payment":   40.0,
    "cache":     200.0,   # high because catalog hits cache for every request
}

# Memory baseline as fraction of limit (healthy idle)
SERVICE_BASELINE_MEM_FRACTION: dict[str, float] = {
    "frontend":  0.25,
    "catalog":   0.30,
    "cart":      0.20,
    "checkout":  0.28,
    "payment":   0.22,
    "cache":     0.40,    # cache holds data, higher base
}

# Per-request memory delta (bytes per request/s per pod, rough steady-state)
SERVICE_MEM_PER_RPS: dict[str, float] = {
    "frontend":  800.0,
    "catalog":   1200.0,
    "cart":      600.0,
    "checkout":  1000.0,
    "payment":   800.0,
    "cache":     300.0,
}


# ── Benign burst state ────────────────────────────────────────────────────────

@dataclass
class BurstState:
    """Tracks an active benign traffic burst."""
    service: str
    multiplier: float    # how much to multiply base RPS
    end_time: float      # sim_time when burst expires


# ── LoadGenerator ─────────────────────────────────────────────────────────────

@dataclass
class LoadGenerator:
    """
    Produces per-service request rates for each physics tick.
    Deterministic given the same seed and sim_time.
    """
    rng: RngStreams
    active_bursts: list[BurstState] = field(default_factory=list)

    # Noise parameters
    noise_amplitude: float = 0.08    # ±8% Gaussian noise
    burst_probability: float = 0.0002  # per tick, per service (~1 burst/hour at 1x)
    burst_multiplier_range: tuple[float, float] = (1.5, 2.5)
    burst_duration_range: tuple[float, float] = (30.0, 180.0)  # sim-seconds

    def request_rate(self, service_id: str, sim_time: float,
                     fault_multiplier: float = 1.0) -> float:
        """
        Return the current request rate (req/s) for a service.

        fault_multiplier > 1: traffic_surge fault applied externally.
        """
        base = SERVICE_BASELINE_RPS.get(service_id, 50.0)
        daily = _daily_multiplier(sim_time)
        noise = 1.0 + self.rng.workload.gauss(0, self.noise_amplitude)

        # Apply active bursts
        burst_mult = 1.0
        for burst in self.active_bursts:
            if burst.service == service_id and sim_time < burst.end_time:
                burst_mult = max(burst_mult, burst.multiplier)

        rate = base * daily * noise * burst_mult * fault_multiplier
        return max(0.0, rate)

    def tick(self, sim_time: float, services: list[str]) -> None:
        """
        Advance load generator by one physics tick.
        May spawn benign bursts; expires old ones.
        """
        # Expire finished bursts
        self.active_bursts = [b for b in self.active_bursts if sim_time < b.end_time]

        # Possibly start a new benign burst
        for svc in services:
            if self.rng.workload.random() < self.burst_probability:
                mult = self.rng.workload.uniform(*self.burst_multiplier_range)
                dur = self.rng.workload.uniform(*self.burst_duration_range)
                self.active_bursts.append(BurstState(
                    service=svc,
                    multiplier=mult,
                    end_time=sim_time + dur,
                ))

    def baseline_rps(self, service_id: str, sim_time: float) -> float:
        """Pure baseline (no noise, no bursts, no faults) — used for normalisation."""
        return SERVICE_BASELINE_RPS.get(service_id, 50.0) * _daily_multiplier(sim_time)
