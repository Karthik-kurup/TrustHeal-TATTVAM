"""
triage/templates.py — Deterministic template-based triage.

Produces structured triage summaries for ASK and ESCALATE incidents
without any LLM dependency. Every claim must reference an evidence source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Hypothesis:
    rank: int
    label: str
    confidence_label: str   # "High" / "Medium" / "Low"
    confidence: float       # 0.0 – 1.0
    evidence_refs: list[str]
    notes: str


@dataclass
class TriageSummary:
    description: str
    hypotheses: list[Hypothesis]
    runbook_steps: list[str]
    command_preview: str
    evidence_refs: list[str]
    gate_decision: str
    vetoes: list[str]


# ── Confidence label helpers ──────────────────────────────────────────────────

def _conf_label(confidence: float) -> str:
    if confidence >= 0.75:
        return "High"
    if confidence >= 0.45:
        return "Medium"
    return "Low"


# ── Runbook knowledge ─────────────────────────────────────────────────────────

_RUNBOOK_STEPS: dict[str, list[str]] = {
    "CPU_OVERLOAD": [
        "1. Verify CPU utilisation on affected pods is consistently above 80%.",
        "2. Check for any traffic surge or load-test running against the service.",
        "3. Scale up the service by +1 replica (bounded by HPA ceiling).",
        "4. If scaling does not resolve within 60s, escalate to on-call.",
    ],
    "TRAFFIC_OVERLOAD": [
        "1. Confirm request rate is >{baseline}x above baseline on the service.",
        "2. Check upstream clients for misconfigured retry storms.",
        "3. Scale up the service by +1 replica.",
        "4. If scaling does not resolve within 60s, escalate.",
    ],
    "MEMORY_EXHAUSTION": [
        "1. Verify memory usage is near or above limit on affected pods.",
        "2. Check for recent deployment that may have introduced a regression.",
        "3. Restart the most memory-heavy pod to clear the working set.",
        "4. Monitor memory after restart; if it climbs again, suspect a leak.",
    ],
    "MEMORY_LEAK": [
        "1. Confirm steady linear growth in memory over at least 2 windows.",
        "2. Correlate with recent deployments.",
        "3. Restart the affected pod to reclaim memory.",
        "4. Schedule a code review for memory management in this service.",
    ],
    "NETWORK_PARTITION": [
        "1. Check network_error_rate and packet loss metrics on affected pod.",
        "2. Verify node networking (NIC / CNI plugin) is functioning.",
        "3. Recreate the affected pod to force re-scheduling on a healthy node.",
        "4. If the issue persists, cordon the node and reschedule all its pods.",
    ],
    "BAD_DEPLOYMENT": [
        "1. Confirm error rate spiked within 10 minutes of the last rollout.",
        "2. Review the diff between current and previous revision.",
        "3. Roll back to the previous Deployment revision.",
        "4. Monitor error rate post-rollback; should drop within 2 windows.",
    ],
    "NODE_FAILURE": [
        "1. Confirm the node is NotReady or Unknown in cluster status.",
        "2. Check node conditions (MemoryPressure / DiskPressure / NetworkUnavailable).",
        "3. Cordon the node to prevent new pod scheduling.",
        "4. Reschedule or recreate affected pods on healthy nodes.",
        "5. Investigate root cause of node failure before un-cordoning.",
    ],
    "UNKNOWN": [
        "1. This fault pattern was not seen during training — manual investigation required.",
        "2. Inspect application logs on affected pods for exceptions or errors.",
        "3. Compare current metrics against the last known-good state.",
        "4. Engage on-call if the service SLA is at risk.",
    ],
    "NORMAL": [
        "1. Health check raised a false positive or is in recovery hysteresis.",
        "2. No action required if metrics return to baseline.",
    ],
}


# ── Template triage builder ───────────────────────────────────────────────────

def build_template_triage(
    incident_id: str,
    service: str,
    diagnosis: dict[str, Any],
    trust: dict[str, Any] | None,
    gate_decision: str,
    vetoes: list[str],
    action_name: str,
    sim_time: float,
) -> TriageSummary:
    """
    Build a deterministic triage summary for an incident.

    Every claim in the description and hypotheses references a named
    evidence source (signal name or metric name).
    """
    label = diagnosis.get("label", "UNKNOWN")
    confidence = float(diagnosis.get("confidence", 0.0))
    probs: dict[str, float] = diagnosis.get("probs", {})
    top_features: dict[str, float] = diagnosis.get("top_features", {})
    mlp_label = diagnosis.get("mlp_label", "")
    xgb_label = diagnosis.get("xgb_label", "")
    evidence_label = diagnosis.get("evidence_label", "")
    mlp_unc = float(diagnosis.get("mlp_uncertainty", 0.5))

    signals = trust or {}
    U = float(signals.get("U", 0.5))
    S = float(signals.get("S", 0.5))
    N = float(signals.get("N", 0.5))
    H = float(signals.get("H", 0.5))
    E = float(signals.get("E", 0.5))
    D = float(signals.get("D", 0.5))
    P = float(signals.get("P_correct", 0.5))

    # ── Description ──────────────────────────────────────────────────────────

    unc_desc = "low" if U < 0.4 else ("moderate" if U < 0.65 else "high")
    nov_desc = "in-distribution" if N < 0.5 else "partially out-of-distribution"
    agree_desc = (
        "all three diagnosers agree" if (mlp_label == xgb_label == label and evidence_label == label)
        else ("ML models agree but evidence rules are silent" if mlp_label == xgb_label
              else "diagnosers partially disagree")
    )

    description = (
        f"Service '{service}' is experiencing degraded health with diagnosis '{label}' "
        f"(ensemble confidence {confidence * 100:.0f}%, P_correct={P * 100:.0f}%). "
        f"MC-dropout uncertainty is {unc_desc} (U={U:.2f}), and the feature distribution is "
        f"{nov_desc} relative to training data (N={N:.2f}). "
        f"The diagnosers report: {agree_desc}. "
    )

    if vetoes:
        description += (
            f"The Trust Gate raised {len(vetoes)} veto(es) ({', '.join(vetoes)}), "
            f"preventing autonomous action and escalating to human review."
        )
    elif gate_decision == "AUTO":
        description += f"The Trust Gate approved automatic execution of '{action_name}'."
    elif gate_decision == "ASK":
        description += f"The Trust Gate is requesting human approval before executing '{action_name}'."
    else:
        description += "This incident has been escalated for manual investigation."

    # ── Hypotheses ────────────────────────────────────────────────────────────

    hypotheses: list[Hypothesis] = []
    sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)

    for rank, (cls, prob) in enumerate(sorted_probs[:3], start=1):
        refs = ["probs"]
        if cls == mlp_label:
            refs.append("MLP")
        if cls == xgb_label:
            refs.append("XGBoost")
        if cls == evidence_label:
            refs.append("evidence_rules")
        hypotheses.append(Hypothesis(
            rank=rank,
            label=cls,
            confidence_label=_conf_label(prob),
            confidence=prob,
            evidence_refs=refs,
            notes=f"P(class)={prob * 100:.1f}%",
        ))

    # ── Runbook steps ─────────────────────────────────────────────────────────

    steps = _RUNBOOK_STEPS.get(label, _RUNBOOK_STEPS["UNKNOWN"])

    # ── Command preview ───────────────────────────────────────────────────────

    cmd_map = {
        "CPU_OVERLOAD":      f"kubectl scale deployment {service} --replicas=+1",
        "TRAFFIC_OVERLOAD":  f"kubectl scale deployment {service} --replicas=+1",
        "MEMORY_EXHAUSTION": f"kubectl rollout restart deployment/{service}",
        "MEMORY_LEAK":       f"kubectl rollout restart deployment/{service}",
        "NETWORK_PARTITION": f"kubectl delete pod -l app={service} --field-selector=status.podIP=<affected>",
        "BAD_DEPLOYMENT":    f"kubectl rollout undo deployment/{service}",
        "NODE_FAILURE":      f"kubectl cordon <failed-node> && kubectl drain <failed-node> --ignore-daemonsets",
        "UNKNOWN":           f"kubectl logs -l app={service} --tail=100",
        "NORMAL":            "# No action required",
    }
    command = cmd_map.get(label, f"kubectl describe pod -l app={service}")

    # ── Evidence refs ─────────────────────────────────────────────────────────

    top_feat_names = sorted(top_features.keys(), key=lambda k: abs(top_features[k]), reverse=True)[:3]
    evidence_refs = ["ensemble_diagnosis", "trust_signals"] + top_feat_names

    return TriageSummary(
        description=description,
        hypotheses=hypotheses,
        runbook_steps=steps,
        command_preview=command,
        evidence_refs=evidence_refs,
        gate_decision=gate_decision,
        vetoes=vetoes,
    )
