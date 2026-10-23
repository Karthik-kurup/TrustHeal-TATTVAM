from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from diagnosis import ensemble
from healing import runbook
from training import evaluate as evaluation
from training import generate_dataset
from trust import gate


def test_unseen_episode_configs_are_not_training_or_normal_episodes():
    configs = generate_dataset._make_unseen_episode_configs()

    assert len(configs) == len(generate_dataset.UNSEEN_FAULT_KINDS) * generate_dataset.UNSEEN_EPISODES_PER_FAULT
    assert all(not config.is_normal for config in configs)
    assert {config.fault_kind for config in configs} == generate_dataset.UNSEEN_FAULT_KINDS
    assert all(
        generate_dataset.FAULT_TRUTH_LABEL[config.fault_kind] == "UNKNOWN"
        for config in configs
    )


def test_unseen_evaluation_runs_the_gate_and_caps_samples_per_fault(monkeypatch):
    def diagnose(_feature_vector):
        return SimpleNamespace(label="UNKNOWN")

    def next_action(_label, service_id, _rung):
        return SimpleNamespace(action="escalate", target=service_id)

    def evaluate_gate(**_kwargs):
        return SimpleNamespace(decision=SimpleNamespace(value="ESCALATE"))

    monkeypatch.setattr(ensemble, "diagnose", diagnose)
    monkeypatch.setattr(runbook, "next_action", next_action)
    monkeypatch.setattr(gate, "evaluate", evaluate_gate)

    features = np.zeros((30, 64), dtype=np.float32)
    faults = np.array(["dns_failure"] * 25 + ["compound"] * 5)
    services = np.array(["checkout"] * len(features))
    report = evaluation._evaluate_unseen_gate(features, faults, services, limit_per_fault=10)

    assert report["total"] == 15
    assert report["escalated"] == 15
    assert report["wrongly_auto"] == 0
    assert report["evaluation_method"] == "trust_gate"
