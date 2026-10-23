/**
 * pages/IncidentWarRoom.tsx — Full pipeline view for each incident.
 *
 * Left sidebar: recent incidents list.
 * Main panel: horizontal pipeline stepper + all detail panels.
 */

import React, { useState, useCallback, useEffect, useRef } from 'react';
import { useIncidentStore, type IncidentApiRecord, type IncidentSummary } from '../state/incidentStore';
import type { TrustSignals } from '../types/ws';
import { requestJson } from '../api/request';

// ── Colours ───────────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  OPEN:       'text-amber-400 bg-amber-900/40 border-amber-700',
  DIAGNOSED:  'text-teal-400 bg-teal-900/40 border-teal-700',
  DECIDING:   'text-blue-400 bg-blue-900/40 border-blue-700',
  WAITING:    'text-purple-400 bg-purple-900/40 border-purple-700',
  ACTING:     'text-blue-400 bg-blue-900/40 border-blue-700',
  VERIFYING:  'text-cyan-400 bg-cyan-900/40 border-cyan-700',
  RESOLVED:   'text-green-400 bg-green-900/40 border-green-700',
  ESCALATED:  'text-purple-400 bg-purple-900/40 border-purple-700',
};

const GATE_COLORS: Record<string, string> = {
  AUTO:     'bg-blue-900 text-blue-200 border-blue-600',
  ASK:      'bg-purple-900 text-purple-200 border-purple-600',
  ESCALATE: 'bg-red-900 text-red-200 border-red-600',
};

function formatTime(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

// ── Pipeline stepper ──────────────────────────────────────────────────────────

const PIPELINE_STAGES = [
  { key: 'OPEN',       label: 'Detected' },
  { key: 'DIAGNOSED',  label: 'Diagnosed' },
  { key: 'DECIDING',   label: 'Trust Check' },
  { key: 'WAITING',    label: 'Decision' },
  { key: 'ACTING',     label: 'Action' },
  { key: 'VERIFYING',  label: 'Verification' },
  { key: 'RESOLVED',   label: 'Resolved' },
];

// Map status to the highest completed stage index
const STATUS_TO_STAGE: Record<string, number> = {
  OPEN: 0, DIAGNOSED: 1, DECIDING: 2, WAITING: 3,
  ACTING: 4, VERIFYING: 5, RESOLVED: 6, ESCALATED: 6,
};

function PipelineStepper({ status }: { status: string }) {
  const current = STATUS_TO_STAGE[status] ?? 0;
  const isEscalated = status === 'ESCALATED';

  return (
    <div className="flex items-center gap-0 mb-6">
      {PIPELINE_STAGES.map((stage, idx) => {
        const done = idx < current;
        const active = idx === current;
        const escalatedHere = isEscalated && idx === 6;
        return (
          <React.Fragment key={stage.key}>
            {idx > 0 && (
              <div
                className={`flex-1 h-0.5 ${
                  done ? 'bg-blue-600' : 'bg-gray-700'
                }`}
              />
            )}
            <div className="flex flex-col items-center gap-1">
              <div
                className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-all ${
                  escalatedHere
                    ? 'border-purple-500 bg-purple-900 text-purple-200'
                    : done
                    ? 'border-blue-500 bg-blue-900 text-blue-200'
                    : active
                    ? 'border-amber-500 bg-amber-900 text-amber-200 ring-2 ring-amber-500/30'
                    : 'border-gray-700 bg-gray-800 text-gray-600'
                }`}
              >
                {done || escalatedHere ? '✓' : idx + 1}
              </div>
              <span
                className={`text-xs whitespace-nowrap ${
                  active ? 'text-amber-400' : done ? 'text-blue-400' : 'text-gray-600'
                }`}
              >
                {escalatedHere ? 'Escalated' : stage.label}
              </span>
            </div>
          </React.Fragment>
        );
      })}
    </div>
  );
}

// ── Trust signals bar ─────────────────────────────────────────────────────────

const SIGNAL_LABELS: Record<keyof TrustSignals, string> = {
  U: 'Uncertainty (U)',
  S: 'Stability (S)',
  N: 'Novelty (N)',
  H: 'Health agree (H)',
  E: 'Evidence agree (E)',
  D: 'Diagnoser agree (D)',
  P_correct: 'P(correct)',
};

const SIGNAL_DESCRIPTIONS: Record<keyof TrustSignals, string> = {
  U: 'MC-dropout uncertainty — lower is more certain',
  S: 'Attribution stability under perturbation — higher is more stable',
  N: 'OOD Mahalanobis distance — higher means out-of-distribution',
  H: 'Health check agrees with diagnosis',
  E: 'Evidence rules agree with ML (1=confirm, 0.5=silent, 0=contradict)',
  D: 'MLP and XGB diagnosers agree',
  P_correct: 'Calibrated probability the diagnosis is correct',
};

// For U and N, higher = worse; for all others higher = better
const SIGNAL_INVERTED: Record<keyof TrustSignals, boolean> = {
  U: true, S: false, N: true, H: false, E: false, D: false, P_correct: false,
};

function signalColor(key: keyof TrustSignals, value: number): string {
  const bad = SIGNAL_INVERTED[key] ? value > 0.6 : value < 0.4;
  const warn = SIGNAL_INVERTED[key] ? value > 0.4 : value < 0.6;
  if (bad) return 'bg-red-500';
  if (warn) return 'bg-amber-500';
  return 'bg-green-500';
}

function TrustPanel({ trust, vetoes }: { trust: NonNullable<IncidentSummary['trust']>; vetoes?: string[] }) {
  const signals = trust.signals;
  const pCorrect = signals.P_correct;

  return (
    <div className="space-y-4">
      {/* P(correct) gauge */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-gray-400 font-medium">P(correct)</span>
          <span
            className={`text-lg font-bold ${
              pCorrect >= 0.8 ? 'text-green-400' : pCorrect >= 0.6 ? 'text-amber-400' : 'text-red-400'
            }`}
          >
            {(pCorrect * 100).toFixed(1)}%
          </span>
        </div>
        <div className="h-3 bg-gray-700 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${
              pCorrect >= 0.8 ? 'bg-green-500' : pCorrect >= 0.6 ? 'bg-amber-500' : 'bg-red-500'
            }`}
            style={{ width: `${pCorrect * 100}%` }}
          />
        </div>
      </div>

      {/* Signal bars */}
      <div className="space-y-1.5">
        {(Object.keys(SIGNAL_LABELS) as (keyof TrustSignals)[])
          .filter((k) => k !== 'P_correct')
          .map((key) => {
            const val = signals[key] as number;
            return (
              <div key={key} title={SIGNAL_DESCRIPTIONS[key]}>
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-xs text-gray-500">{key}</span>
                  <span className="text-xs text-gray-300">{val.toFixed(2)}</span>
                </div>
                <div className="h-1.5 bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${signalColor(key, val)}`}
                    style={{ width: `${val * 100}%` }}
                  />
                </div>
              </div>
            );
          })}
      </div>

      {/* Vetoes */}
      {vetoes && vetoes.length > 0 && (
        <div className="space-y-1">
          <span className="text-xs text-gray-400 font-medium">Active Vetoes</span>
          {vetoes.map((v) => (
            <div key={v} className="flex items-center gap-2 bg-red-900/30 border border-red-800 rounded px-2 py-1 text-xs">
              <span className="text-red-400">&#9888;</span>
              <span className="text-red-300 font-mono">{v}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Diagnosis panel ───────────────────────────────────────────────────────────

function DiagnosisPanel({ diagnosis }: { diagnosis: NonNullable<IncidentSummary['diagnosis']> }) {
  const sortedProbs = Object.entries(diagnosis.probs).sort((a, b) => b[1] - a[1]);

  return (
    <div className="space-y-4">
      {/* Ensemble result */}
      <div className="flex items-center gap-3">
        <span className="text-sm font-semibold text-teal-300">{diagnosis.label}</span>
        <span className="text-xs text-gray-400">
          {(diagnosis.confidence * 100).toFixed(1)}% confidence
        </span>
        {diagnosis.abstained && (
          <span className="text-xs bg-gray-700 text-gray-400 px-2 py-0.5 rounded">ABSTAINED</span>
        )}
      </div>

      {/* Diagnoser agreement row */}
      <div className="grid grid-cols-3 gap-2 text-xs">
        {[
          { name: 'MLP-MCD', label: diagnosis.mlp_label },
          { name: 'XGBoost', label: diagnosis.xgb_label },
          { name: 'Evidence', label: diagnosis.evidence_label },
        ].map(({ name, label }) => (
          <div
            key={name}
            className={`rounded border px-2 py-1.5 text-center ${
              label === diagnosis.label
                ? 'border-teal-700 bg-teal-900/30 text-teal-300'
                : 'border-gray-700 bg-gray-800 text-gray-400'
            }`}
          >
            <div className="text-gray-500 text-xs mb-0.5">{name}</div>
            <div className="font-medium truncate">{label || '—'}</div>
          </div>
        ))}
      </div>

      {/* Class probability bars */}
      <div className="space-y-1">
        <span className="text-xs text-gray-400 font-medium">Class probabilities</span>
        {sortedProbs.slice(0, 6).map(([cls, prob]) => (
          <div key={cls}>
            <div className="flex items-center justify-between mb-0.5">
              <span className="text-xs font-mono text-gray-400 w-36 truncate">{cls}</span>
              <span className="text-xs text-gray-300">{(prob * 100).toFixed(1)}%</span>
            </div>
            <div className="h-1.5 bg-gray-700 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${
                  cls === diagnosis.label ? 'bg-teal-500' : 'bg-gray-600'
                }`}
                style={{ width: `${prob * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>

      {/* Uncertainty */}
      <div className="text-xs text-gray-500">
        MC-dropout uncertainty: {(diagnosis.mlp_uncertainty * 100).toFixed(1)}%
      </div>

      {/* Top feature contributions */}
      {Object.keys(diagnosis.top_features).length > 0 && (
        <div className="space-y-1">
          <span className="text-xs text-gray-400 font-medium">Top feature contributions</span>
          {Object.entries(diagnosis.top_features)
            .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
            .slice(0, 5)
            .map(([feat, val]) => (
              <div key={feat} className="flex items-center justify-between text-xs">
                <span className="text-gray-500 font-mono truncate w-40">{feat}</span>
                <span className={val > 0 ? 'text-green-400' : 'text-red-400'}>
                  {val > 0 ? '+' : ''}{val.toFixed(3)}
                </span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

// ── Decision card ─────────────────────────────────────────────────────────────

function DecisionCard({
  decision,
  status,
}: {
  decision: NonNullable<IncidentSummary['decision']>;
  status: string;
}) {
  return (
    <div className="space-y-3">
      {/* Gate decision badge */}
      <div className="flex items-center gap-3">
        <span
          className={`px-3 py-1.5 rounded-lg border text-sm font-bold ${
            GATE_COLORS[decision.gate_decision] ?? 'bg-gray-800 text-gray-300 border-gray-600'
          }`}
        >
          {decision.gate_decision}
        </span>
        <span className={`text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-300`}>
          {decision.action_risk}
        </span>
      </div>

      {/* Proposed action */}
      <div className="bg-gray-900 border border-gray-700 rounded p-3 text-xs space-y-1">
        <div className="flex justify-between">
          <span className="text-gray-500">Action</span>
          <span className="text-blue-300 font-mono">{decision.action_name}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Target</span>
          <span className="text-gray-300">{decision.action_target}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">P(correct)</span>
          <span className="text-gray-300">{(decision.p_correct * 100).toFixed(1)}%</span>
        </div>
      </div>

      {/* Vetoes summary */}
      {decision.vetoes.length > 0 && (
        <div className="text-xs text-red-400 bg-red-900/20 border border-red-800 rounded p-2">
          Vetoes: {decision.vetoes.join(', ')}
        </div>
      )}
      {/* Human approval is available in the response-plan dialog. */}
      {decision.gate_decision === 'ASK' && status === 'WAITING' && (
        <p className="text-xs text-purple-300">
          Review the response plan above to inspect the steps before approving or rejecting.
        </p>
      )}
    </div>
  );
}

// ── Action log ────────────────────────────────────────────────────────────────

function ActionLog({ actions }: { actions: IncidentSummary['actions'] }) {
  if (actions.length === 0) {
    return <p className="text-xs text-gray-600">No actions executed yet.</p>;
  }
  return (
    <div className="space-y-2">
      {actions.map((a, i) => (
        <div
          key={i}
          className={`rounded border px-3 py-2 text-xs ${
            a.result_ok
              ? 'bg-green-900/20 border-green-800'
              : 'bg-red-900/20 border-red-800'
          }`}
        >
          <div className="flex items-center justify-between mb-1">
            <span className="font-mono text-blue-300">{a.action}</span>
            <span className={`font-semibold ${a.result_ok ? 'text-green-400' : 'text-red-400'}`}>
              {a.result_ok ? 'OK' : 'FAILED'}
            </span>
          </div>
          <div className="flex gap-4 text-gray-500">
            <span>target: <span className="text-gray-300">{a.target}</span></span>
            <span>risk: <span className="text-gray-300">{a.risk_class}</span></span>
            <span>attempt: <span className="text-gray-300">#{a.attempt}</span></span>
          </div>
          {a.result_detail && (
            <div className="text-gray-400 mt-1">{a.result_detail}</div>
          )}
          {a.undo_executed && (
            <div className="text-amber-400 mt-1">↩ Undo executed</div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Verification panel ────────────────────────────────────────────────────────

function VerificationPanel({ verification }: { verification: NonNullable<IncidentSummary['verification']> }) {
  const statusColor =
    verification.health_status === 'OK'
      ? 'text-green-400'
      : verification.health_status === 'WORSE'
      ? 'text-red-400'
      : 'text-amber-400';

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <span className={`text-lg font-bold ${statusColor}`}>{verification.health_status}</span>
        {verification.resolved && (
          <span className="text-xs bg-green-900 text-green-300 px-2 py-0.5 rounded">RESOLVED</span>
        )}
      </div>
      {Object.keys(verification.metric_snapshot).length > 0 && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          {Object.entries(verification.metric_snapshot).map(([k, v]) => (
            <div key={k} className="flex justify-between">
              <span className="text-gray-500">{k}</span>
              <span className="text-gray-300">{v.toFixed(3)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface IncidentProposal {
  incident_id: string;
  service: string;
  diagnosis: { label: string; confidence: number; abstained: boolean };
  proposal: {
    action: string;
    target: string;
    risk_class: string;
    decision: 'AUTO' | 'ASK' | 'ESCALATE';
    p_correct: number;
    reason: string;
    vetoes: string[];
    steps: string[];
  };
  alternatives: Array<{
    action: string;
    description: string;
    risk_class: string;
    mode: string;
    selected_by_runbook: boolean;
  }>;
  stage: string;
  kill_switch: boolean;
  generated_at: number;
}

function ReviewPlanButton({ incident }: { incident: IncidentSummary }) {
  const [proposal, setProposal] = useState<IncidentProposal | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [approving, setApproving] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [approved, setApproved] = useState(false);
  const [rejected, setRejected] = useState(false);
  const [error, setError] = useState('');
  const autoPromptedId = useRef<string | null>(null);
  const canReview = incident.status !== 'RESOLVED';

  const openReview = useCallback(async () => {
    setIsOpen(true);
    setLoading(true);
    setError('');
    setApproved(false);
    setRejected(false);
    try {
      const result = await requestJson<IncidentProposal>(
        `/api/incidents/${encodeURIComponent(incident.incident_id)}/proposal`,
      );
      setProposal(result);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Unable to build response plan');
    } finally {
      setLoading(false);
    }
  }, [incident.incident_id]);

  useEffect(() => {
    if (
      incident.status === 'WAITING' &&
      incident.gate_decision === 'ASK' &&
      autoPromptedId.current !== incident.incident_id
    ) {
      autoPromptedId.current = incident.incident_id;
      void openReview();
    }
  }, [incident.gate_decision, incident.incident_id, incident.status, openReview]);

  const approveProposal = async () => {
    if (!proposal) {
      setError('The response plan is not ready yet');
      return;
    }
    setApproving(true);
    setError('');
    try {
      await requestJson(`/api/incidents/${encodeURIComponent(incident.incident_id)}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: proposal.proposal.action,
          target: proposal.proposal.target,
        }),
      });
      setApproved(true);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Approval failed');
    } finally {
      setApproving(false);
    }
  };

  const rejectProposal = async () => {
    setRejecting(true);
    setError('');
    try {
      await requestJson(`/api/incidents/${encodeURIComponent(incident.incident_id)}/reject`, {
        method: 'POST',
      });
      setRejected(true);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Rejection failed');
    } finally {
      setRejecting(false);
    }
  };

  if (!canReview) return null;

  return (
    <>
      <button
        type="button"
        onClick={openReview}
        className="px-3 py-1.5 rounded border border-blue-600 bg-blue-900/50 hover:bg-blue-800 text-blue-100 text-xs font-semibold"
      >
        Review response plan
      </button>
      {isOpen && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setIsOpen(false);
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="response-plan-title"
            className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-xl border border-gray-600 bg-gray-900 p-5 shadow-2xl"
          >
            <div className="flex items-start justify-between gap-3 mb-4">
              <div>
                <h2 id="response-plan-title" className="text-lg font-semibold text-gray-100">
                  Review response plan
                </h2>
                <p className="text-xs text-gray-400 mt-1">
                  {incident.service} · {incident.incident_id}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                className="rounded px-2 py-1 text-gray-400 hover:bg-gray-800 hover:text-white"
                aria-label="Close response plan"
              >
                ✕
              </button>
            </div>

            {loading && <p className="text-sm text-gray-400">Building a fresh plan from current telemetry…</p>}
            {error && <p className="mb-3 rounded border border-red-800 bg-red-950/50 p-2 text-sm text-red-300" role="alert">{error}</p>}
            {proposal && (
              <div className="space-y-4">
                <div className="rounded-lg border border-gray-700 bg-gray-800 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <p className="text-xs uppercase tracking-wide text-gray-500">Current diagnosis</p>
                      <p className="font-semibold text-teal-300">{proposal.diagnosis.label}</p>
                    </div>
                    <span className="text-xs text-gray-400">
                      {proposal.stage}{proposal.kill_switch ? ' · Kill switch ON' : ''}
                    </span>
                  </div>
                </div>

                <div className="rounded-lg border border-blue-800 bg-blue-950/30 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <p className="text-xs uppercase tracking-wide text-gray-500">Runbook recommendation</p>
                      <p className="text-base font-semibold text-blue-200">
                        {proposal.proposal.action} <span className="text-gray-400">→</span> {proposal.proposal.target}
                      </p>
                    </div>
                    <span className={`rounded border px-2 py-1 text-xs font-bold ${GATE_COLORS[proposal.proposal.decision]}`}>
                      {proposal.proposal.decision} · {proposal.proposal.risk_class}
                    </span>
                  </div>
                  <p className="mt-2 text-xs text-gray-300">{proposal.proposal.reason}</p>
                  <p className="mt-1 text-xs text-gray-400">
                    Trust score: {(proposal.proposal.p_correct * 100).toFixed(1)}% P(correct)
                  </p>
                  {proposal.proposal.vetoes.length > 0 && (
                    <p className="mt-2 text-xs text-red-300">
                      Safety vetoes: {proposal.proposal.vetoes.join(', ')}
                    </p>
                  )}
                </div>

                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">What the system will do</h3>
                  <ol className="space-y-1.5">
                    {proposal.proposal.steps.map((step, index) => (
                      <li key={step} className="flex gap-2 text-sm text-gray-300">
                        <span className="font-mono text-gray-500">{index + 1}.</span>
                        {step}
                      </li>
                    ))}
                  </ol>
                </div>

                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Other possible actions</h3>
                  <p className="mb-2 text-xs text-gray-500">
                    Alternatives are not selected by the runbook and have not been checked by the Trust Gate.
                  </p>
                  <div className="space-y-1.5">
                    {proposal.alternatives.map((option) => (
                      <div key={option.action} className="flex flex-wrap items-center justify-between gap-2 rounded border border-gray-700 bg-gray-800/60 px-3 py-2 text-xs">
                        <span className="text-gray-200">{option.action} <span className="text-gray-500">— {option.description}</span></span>
                        <span className="text-gray-400">{option.risk_class} · {option.mode} at {proposal.stage}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {proposal.proposal.decision === 'ASK' && incident.status === 'WAITING' && (
                  <div className="flex flex-wrap justify-end gap-2 border-t border-gray-700 pt-3">
                    <button
                      type="button"
                      onClick={approveProposal}
                      disabled={approving || approved || rejected}
                      className="rounded bg-green-800 px-4 py-2 text-sm font-semibold text-green-100 hover:bg-green-700 disabled:opacity-50"
                    >
                      {approved ? 'Approved — waiting for next simulation tick' : approving ? 'Approving…' : `Approve ${proposal.proposal.action}`}
                    </button>
                    <button
                      type="button"
                      onClick={rejectProposal}
                      disabled={rejecting || rejected || approved}
                      className="rounded bg-red-900 px-4 py-2 text-sm font-semibold text-red-200 hover:bg-red-800 disabled:opacity-50"
                    >
                      {rejected ? 'Rejected — escalating' : rejecting ? 'Rejecting…' : 'Reject and escalate'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setIsOpen(false)}
                      className="rounded border border-gray-600 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800"
                    >
                      Not now
                    </button>
                  </div>
                )}
                {proposal.proposal.decision !== 'ASK' && (
                  <p className="border-t border-gray-700 pt-3 text-xs text-gray-400">
                    {proposal.proposal.decision === 'ESCALATE'
                      ? 'The current mode or a safety veto blocks execution. Approval cannot override a safety veto; the system will escalate for operator review.'
                      : 'The current policy permits this action automatically after the remaining checks pass.'}
                  </p>
                )}
              </div>
            )}
          </section>
        </div>
      )}
    </>
  );
}

// ── Incident detail panel ─────────────────────────────────────────────────────

function IncidentDetail({ incident }: { incident: IncidentSummary }) {
  const [showGroundTruth] = useState(false);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold text-gray-100 capitalize">{incident.service}</h2>
          <div className="text-xs text-gray-500 font-mono">{incident.incident_id}</div>
          <div className="text-xs text-gray-500 mt-0.5">
            Opened at t={formatTime(incident.opened_at)}
            {incident.closed_at && ` · Closed at t=${formatTime(incident.closed_at)}`}
            {incident.time_to_recover && ` · TTR ${incident.time_to_recover.toFixed(0)}s`}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <ReviewPlanButton incident={incident} />
          <span
            className={`text-xs px-2 py-1 rounded border ${
              STATUS_COLORS[incident.status] ?? 'text-gray-400 bg-gray-800 border-gray-700'
            }`}
          >
            {incident.status}
          </span>
        </div>
      </div>

      {/* Pipeline stepper */}
      <PipelineStepper status={incident.status} />

      {/* Main grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Diagnosis */}
        {incident.diagnosis && (
          <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Diagnosis
            </h3>
            <DiagnosisPanel diagnosis={incident.diagnosis} />
          </div>
        )}

        {/* Trust gate */}
        {incident.trust && (
          <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Trust Gate
            </h3>
            <TrustPanel trust={incident.trust} vetoes={incident.trust.vetoes} />
          </div>
        )}

        {/* Decision */}
        {incident.decision && (
          <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
              Decision
            </h3>
            <DecisionCard
              decision={incident.decision}
              status={incident.status}
            />
          </div>
        )}

        {/* Actions */}
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Action Log
          </h3>
          <ActionLog actions={incident.actions} />
        </div>
      </div>

      {/* Verification */}
      {incident.verification && (
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
            Verification
          </h3>
          <VerificationPanel verification={incident.verification} />
        </div>
      )}
    </div>
  );
}

// ── Incident list sidebar ─────────────────────────────────────────────────────

function IncidentListItem({
  incident,
  selected,
  onClick,
}: {
  incident: IncidentSummary;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 rounded border transition-colors ${
        selected
          ? 'bg-gray-700 border-blue-600'
          : 'bg-gray-800 border-gray-700 hover:bg-gray-700/50'
      }`}
    >
      <div className="flex items-center justify-between mb-0.5">
        <span className="text-xs font-medium text-gray-200 capitalize">{incident.service}</span>
        <span
          className={`text-xs px-1.5 py-0.5 rounded ${
            incident.status === 'RESOLVED'
              ? 'bg-green-900 text-green-400'
              : incident.status === 'ESCALATED'
              ? 'bg-purple-900 text-purple-400'
              : incident.status === 'WAITING'
              ? 'bg-purple-900 text-purple-300 animate-pulse'
              : 'bg-amber-900 text-amber-400'
          }`}
        >
          {incident.status}
        </span>
      </div>
      <div className="text-xs text-gray-500 truncate">{incident.incident_id}</div>
      {incident.diagnosis && (
        <div className="text-xs text-teal-400 mt-0.5 truncate">{incident.diagnosis.label}</div>
      )}
      {incident.gate_decision && (
        <div
          className={`text-xs mt-0.5 font-medium ${
            incident.gate_decision === 'AUTO'
              ? 'text-blue-400'
              : incident.gate_decision === 'ASK'
              ? 'text-purple-400'
              : 'text-red-400'
          }`}
        >
          {incident.gate_decision}
          {incident.action_name ? ` · ${incident.action_name}` : ''}
        </div>
      )}
    </button>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function IncidentWarRoom() {
  const allIncidents = useIncidentStore((s) => s.recentIncidents(50));
  const hydrateIncidents = useIncidentStore((s) => s.hydrateIncidents);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    let active = true;
    requestJson<IncidentApiRecord[]>('/api/incidents')
      .then((records) => {
        if (active) hydrateIncidents(records);
      })
      .catch((error: unknown) => {
        if (active) {
          setLoadError(error instanceof Error ? error.message : 'Unable to load incidents');
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [hydrateIncidents]);

  const selected = allIncidents.find((i) => i.incident_id === selectedId) ?? allIncidents[0] ?? null;

  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
  }, []);

  // Auto-select first open or most recent incident
  React.useEffect(() => {
    if (!selectedId && allIncidents.length > 0) {
      const open = allIncidents.find((i) => !['RESOLVED', 'ESCALATED'].includes(i.status));
      setSelectedId(open?.incident_id ?? allIncidents[0].incident_id);
    }
  }, [allIncidents, selectedId]);

  useEffect(() => {
    const pendingApproval = allIncidents.find(
      (incident) => incident.status === 'WAITING' && incident.gate_decision === 'ASK',
    );
    if (pendingApproval && pendingApproval.incident_id !== selectedId) {
      setSelectedId(pendingApproval.incident_id);
    }
  }, [allIncidents, selectedId]);

  return (
    <div className="flex h-[calc(100vh-3rem)] overflow-hidden">
      {/* Sidebar */}
      <div className="w-64 border-r border-gray-700 flex flex-col bg-gray-900 shrink-0">
        <div className="p-3 border-b border-gray-700">
          <h1 className="text-sm font-semibold text-gray-200">Incident War Room</h1>
          <p className="text-xs text-gray-500 mt-0.5">{allIncidents.length} incidents</p>
        </div>
        <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
          {loadError && (
            <p className="text-xs text-red-300 bg-red-950/60 border border-red-800 rounded p-2" role="alert">
              Unable to load incidents: {loadError}
            </p>
          )}
          {allIncidents.length === 0 && (
            <p className="text-xs text-gray-600 p-2">
              {loading ? 'Loading incidents…' : 'No incidents yet.'}
            </p>
          )}
          {allIncidents.map((inc) => (
            <IncidentListItem
              key={inc.incident_id}
              incident={inc}
              selected={selected?.incident_id === inc.incident_id}
              onClick={() => handleSelect(inc.incident_id)}
            />
          ))}
        </div>
      </div>

      {/* Main detail area */}
      <div className="flex-1 overflow-y-auto p-6">
        {selected ? (
          <IncidentDetail incident={selected} />
        ) : (
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-600">Select an incident to inspect.</p>
          </div>
        )}
      </div>
    </div>
  );
}
