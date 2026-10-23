/**
 * pages/ControlCenter.tsx — Autonomy stage, thresholds, kill switch, audit log.
 */

import React, { useState, useEffect } from 'react';
import { useSimStore, type AutonomyStage } from '../state/simStore';
import { requestJson } from '../api/request';

// ── Types ─────────────────────────────────────────────────────────────────────

interface ThresholdState {
  R1_auto: number;
  R2_auto: number;
  precision_target_R1?: number;
  precision_target_R2?: number;
}
interface LimitsState {
  actions_per_service_per_hour: number;
  actions_per_cluster_per_hour: number;
  cooldown_seconds: number;
  max_actions_per_service_per_hour?: number;
  max_actions_per_cluster_per_hour?: number;
  min_cooldown_seconds?: number;
  max_cooldown_seconds?: number;
}

interface AuditEntry {
  id: string;
  sim_time: number;
  event_type: string;
  service: string;
  action: string;
  target: string;
  risk_class: string;
  gate_decision: string;
  result: string;
  detail: string;
  autonomy_stage: string;
}

// ── Stage info ────────────────────────────────────────────────────────────────

const STAGES = ['S0', 'S1', 'S2', 'S3', 'S4'] as const;
const STAGE_DESCRIPTIONS: Record<string, string> = {
  S0: 'Shadow — observe only, no actions taken',
  S1: 'Recommend — narrate what actions would be taken',
  S2: 'Approve — every action requires human approval',
  S3: 'Auto Low-risk — R0/R1 automatic, R2+ require approval',
  S4: 'Auto Medium-risk — R0/R1/R2 automatic, R3 requires approval',
};

// ── Action risk table ─────────────────────────────────────────────────────────

const ACTION_RISK_TABLE = [
  { action: 'no_op',      risk: 'R0', reversible: 'Yes',    s3: 'AUTO', s4: 'AUTO' },
  { action: 'escalate',   risk: 'R0', reversible: 'Yes',    s3: 'AUTO', s4: 'AUTO' },
  { action: 'restart',    risk: 'R1', reversible: 'Yes',    s3: 'AUTO', s4: 'AUTO' },
  { action: 'scale_up',   risk: 'R1', reversible: 'Yes',    s3: 'AUTO', s4: 'AUTO' },
  { action: 'recreate',   risk: 'R2', reversible: 'Mostly', s3: 'ASK',  s4: 'AUTO' },
  { action: 'rollback',   risk: 'R2', reversible: 'Yes',    s3: 'ASK',  s4: 'AUTO' },
  { action: 'reschedule', risk: 'R3', reversible: 'Partly', s3: 'ASK',  s4: 'ASK'  },
];

function currentActionMode(risk: string, stage: AutonomyStage, killSwitch: boolean): string {
  if (risk === 'R0') return 'AUTO';
  if (killSwitch || stage === 'S0' || stage === 'S1') return 'ESCALATE';
  if (stage === 'S2' || risk === 'R3') return 'ASK';
  if (stage === 'S3' && risk === 'R2') return 'ASK';
  return 'AUTO';
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ControlCenter() {
  const { autonomyStage, killSwitch, setControlState } = useSimStore();
  const [thresholds, setThresholds] = useState<ThresholdState>({
    R1_auto: 0.70,
    R2_auto: 0.85,
    precision_target_R1: 0.95,
    precision_target_R2: 0.99,
  });
  const [limits, setLimits] = useState<LimitsState>({
    actions_per_service_per_hour: 5,
    actions_per_cluster_per_hour: 20,
    cooldown_seconds: 60,
    max_actions_per_service_per_hour: 20,
    max_actions_per_cluster_per_hour: 100,
    min_cooldown_seconds: 0,
    max_cooldown_seconds: 300,
  });
  const [auditLog, setAuditLog] = useState<AuditEntry[]>([]);
  const [thresholdSaving, setThresholdSaving] = useState(false);
  const [limitsSaving, setLimitsSaving] = useState(false);
  const [stageSaving, setStageSaving] = useState(false);
  const [killSwitchSaving, setKillSwitchSaving] = useState(false);
  const [requestError, setRequestError] = useState('');
  const [savedMessage, setSavedMessage] = useState('');

  // Load initial state
  useEffect(() => {
    let active = true;
    void Promise.all([
      requestJson<ThresholdState>('/api/control/thresholds'),
      requestJson<LimitsState>('/api/control/limits'),
      requestJson<AuditEntry[]>('/api/audit?limit=50'),
      requestJson<{ autonomy_stage: string; kill_switch: boolean }>('/api/control/state'),
    ])
      .then(([loadedThresholds, loadedLimits, loadedAudit, control]) => {
        if (!active) return;
        setThresholds(loadedThresholds);
        setLimits(loadedLimits);
        setAuditLog(loadedAudit);
        setControlState(control.autonomy_stage as AutonomyStage, control.kill_switch);
      })
      .catch((error: unknown) => {
        if (active) setRequestError(error instanceof Error ? error.message : 'Unable to load control settings');
      });
    return () => { active = false; };
  }, [setControlState]);

  const setStage = async (stage: string) => {
    setRequestError('');
    setSavedMessage('');
    setStageSaving(true);
    try {
      const result = await requestJson<{ autonomy_stage: AutonomyStage }>('/api/control/mode', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ autonomy_stage: stage }),
      });
      setControlState(result.autonomy_stage, killSwitch);
      setSavedMessage(`${result.autonomy_stage} autonomy stage applied to the running simulation.`);
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : 'Unable to change autonomy stage');
    } finally {
      setStageSaving(false);
    }
  };

  const toggleKillSwitch = async () => {
    const enabled = !killSwitch;
    setRequestError('');
    setSavedMessage('');
    setKillSwitchSaving(true);
    try {
      await requestJson('/api/control/killswitch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      setControlState(autonomyStage, enabled);
      setSavedMessage(`Kill switch ${enabled ? 'enabled' : 'disabled'} for the running simulation.`);
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : 'Unable to change kill switch');
    } finally {
      setKillSwitchSaving(false);
    }
  };

  const saveThresholds = async () => {
    setThresholdSaving(true);
    setRequestError('');
    setSavedMessage('');
    try {
      const result = await requestJson<{ thresholds: ThresholdState }>('/api/control/thresholds', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ R1_auto: thresholds.R1_auto, R2_auto: thresholds.R2_auto }),
      });
      setThresholds(result.thresholds);
      setSavedMessage('Precision thresholds saved and active.');
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : 'Unable to save thresholds');
    } finally {
      setThresholdSaving(false);
    }
  };

  const saveLimits = async () => {
    setLimitsSaving(true);
    setRequestError('');
    setSavedMessage('');
    try {
      const result = await requestJson<{ limits: LimitsState }>('/api/control/limits', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          actions_per_service_per_hour: limits.actions_per_service_per_hour,
          actions_per_cluster_per_hour: limits.actions_per_cluster_per_hour,
          cooldown_seconds: limits.cooldown_seconds,
        }),
      });
      setLimits(result.limits);
      setSavedMessage('Rate limits and cooldown saved and active.');
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : 'Unable to save limits');
    } finally {
      setLimitsSaving(false);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-xl font-semibold text-gray-100">Control Center</h1>
      {requestError && (
        <div className="rounded border border-red-700 bg-red-900/20 p-3 text-sm text-red-300" role="alert">
          {requestError}
        </div>
      )}
      {savedMessage && (
        <div className="rounded border border-green-700 bg-green-900/20 p-3 text-sm text-green-300" role="status">
          {savedMessage} Settings are in-memory and reset when the API server restarts.
        </div>
      )}

      {/* Kill switch */}
      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">Global Kill Switch</h2>
        <div className="flex items-center gap-4">
          <button
            onClick={toggleKillSwitch}
            disabled={killSwitchSaving}
            className={`px-6 py-2 rounded font-semibold text-sm transition-all border ${
              killSwitch
                ? 'bg-red-600 border-red-400 text-white'
                : 'bg-gray-800 border-red-700 text-red-400 hover:bg-red-900'
            }`}
          >
            {killSwitchSaving
              ? 'Updating…'
              : killSwitch ? '⚠ KILL SWITCH ON — Click to Restore' : '⚠ Activate Kill Switch'}
          </button>
          <p className="text-xs text-gray-500">
            {killSwitch
              ? 'All AUTO actions suspended. System is in recommend-only mode.'
              : 'When activated, all autonomous actions are immediately suspended.'}
          </p>
        </div>
      </div>

      {/* Autonomy stage + action risk table */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-gray-400 mb-3">Autonomy Stage</h2>
          <div className="space-y-2">
            {STAGES.map((stage) => (
              <button
                key={stage}
                onClick={() => setStage(stage)}
                disabled={stageSaving}
                className={`w-full flex items-center gap-3 px-4 py-2 rounded text-sm text-left transition-colors border ${
                  autonomyStage === stage
                    ? 'bg-blue-900 border-blue-600 text-blue-200'
                    : 'bg-gray-700 border-gray-600 text-gray-300 hover:bg-gray-600'
                } disabled:opacity-60`}
              >
                <span className="font-mono font-bold w-4 shrink-0">{stage}</span>
                <span className="text-xs">{STAGE_DESCRIPTIONS[stage]}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-gray-400 mb-3">Action Risk &amp; Mode</h2>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 border-b border-gray-700">
                <th className="text-left py-1 pr-2">Action</th>
                <th className="text-left py-1 pr-2">Risk</th>
                <th className="text-left py-1 pr-2">Rev.</th>
                <th className="text-left py-1 pr-2">Now ({autonomyStage})</th>
                <th className="text-left py-1 pr-2">S3</th>
                <th className="text-left py-1">S4</th>
              </tr>
            </thead>
            <tbody>
              {ACTION_RISK_TABLE.map((row) => (
                <tr key={row.action} className="border-b border-gray-800 text-gray-300">
                  <td className="py-1 pr-2 font-mono">{row.action}</td>
                  <td className="py-1 pr-2 text-gray-500">{row.risk}</td>
                  <td className="py-1 pr-2 text-gray-500">{row.reversible}</td>
                  <td className={`py-1 pr-2 font-medium ${
                    currentActionMode(row.risk, autonomyStage, killSwitch) === 'AUTO'
                      ? 'text-blue-400'
                      : currentActionMode(row.risk, autonomyStage, killSwitch) === 'ASK'
                        ? 'text-purple-400'
                        : 'text-red-400'
                  }`}>
                    {currentActionMode(row.risk, autonomyStage, killSwitch)}
                  </td>
                  <td className={`py-1 pr-2 font-medium ${row.s3 === 'AUTO' ? 'text-blue-400' : 'text-purple-400'}`}>
                    {row.s3}
                  </td>
                  <td className={`py-1 font-medium ${row.s4 === 'AUTO' ? 'text-blue-400' : 'text-purple-400'}`}>
                    {row.s4}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Thresholds */}
      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">Precision Thresholds</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            <label className="text-xs text-gray-500 block mb-1">
              R1 Auto threshold: P(correct) &ge; {thresholds.R1_auto.toFixed(2)}
            </label>
            <input
              type="range" min={0.5} step={0.01}
              max={Math.min(0.99, thresholds.R2_auto)}
              value={thresholds.R1_auto}
              onChange={(e) => setThresholds((t) => ({ ...t, R1_auto: parseFloat(e.target.value) }))}
              className="w-full"
            />
            <p className="text-xs text-gray-600 mt-1">Policy target: {(thresholds.precision_target_R1 ?? 0.95) * 100}% precision for R1. This slider sets the required P(correct), not measured precision.</p>
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">
              R2 Auto threshold: P(correct) &ge; {thresholds.R2_auto.toFixed(2)}
            </label>
            <input
              type="range" max={0.99} step={0.01}
              min={Math.max(0.5, thresholds.R1_auto)}
              value={thresholds.R2_auto}
              onChange={(e) => setThresholds((t) => ({ ...t, R2_auto: parseFloat(e.target.value) }))}
              className="w-full"
            />
            <p className="text-xs text-gray-600 mt-1">Policy target: {(thresholds.precision_target_R2 ?? 0.99) * 100}% precision for R2. This slider sets the required P(correct), not measured precision.</p>
          </div>
        </div>
        <button
          onClick={saveThresholds}
          disabled={thresholdSaving}
          className="mt-3 px-4 py-1.5 rounded bg-blue-800 hover:bg-blue-700 text-blue-100 text-xs font-medium disabled:opacity-50"
        >
          {thresholdSaving ? 'Saving…' : 'Save Thresholds'}
        </button>
      </div>

      {/* Rate limits */}
      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">Rate Limits &amp; Cooldowns</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div>
            <label className="text-xs text-gray-500 block mb-1">
              Per-service budget: {limits.actions_per_service_per_hour}/hr
            </label>
            <input
              type="range" min={1} max={limits.max_actions_per_service_per_hour ?? 20} step={1}
              value={limits.actions_per_service_per_hour}
              onChange={(e) => setLimits((l) => ({ ...l, actions_per_service_per_hour: parseInt(e.target.value) }))}
              className="w-full"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">
              Cluster budget: {limits.actions_per_cluster_per_hour}/hr
            </label>
            <input
              type="range" min={1} max={limits.max_actions_per_cluster_per_hour ?? 100} step={1}
              value={limits.actions_per_cluster_per_hour}
              onChange={(e) => setLimits((l) => ({ ...l, actions_per_cluster_per_hour: parseInt(e.target.value) }))}
              className="w-full"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">
              Cooldown: {limits.cooldown_seconds}s
            </label>
            <input
              type="range"
              min={limits.min_cooldown_seconds ?? 0}
              max={limits.max_cooldown_seconds ?? 300}
              step={limits.max_cooldown_seconds === 300 ? 10 : 1}
              value={limits.cooldown_seconds}
              onChange={(e) => setLimits((l) => ({ ...l, cooldown_seconds: parseFloat(e.target.value) }))}
              className="w-full"
            />
          </div>
        </div>
        <button
          onClick={saveLimits}
          disabled={limitsSaving}
          className="mt-3 px-4 py-1.5 rounded bg-blue-800 hover:bg-blue-700 text-blue-100 text-xs font-medium disabled:opacity-50"
        >
          {limitsSaving ? 'Saving…' : 'Save Limits'}
        </button>
      </div>

      {/* Audit log */}
      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">Audit Log</h2>
        {auditLog.length === 0 ? (
          <p className="text-xs text-gray-600">No audit entries yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-gray-500 border-b border-gray-700">
                  <th className="text-left py-1 pr-3">Time</th>
                  <th className="text-left py-1 pr-3">Type</th>
                  <th className="text-left py-1 pr-3">Service</th>
                  <th className="text-left py-1 pr-3">Action</th>
                  <th className="text-left py-1 pr-3">Result</th>
                  <th className="text-left py-1">Detail</th>
                </tr>
              </thead>
              <tbody>
                {auditLog.map((e, i) => (
                  <tr key={i} className="border-b border-gray-800 text-gray-300">
                    <td className="py-1 pr-3 font-mono">{Math.round(e.sim_time)}s</td>
                    <td className="py-1 pr-3 text-gray-500">{e.event_type}</td>
                    <td className="py-1 pr-3">{e.service || '—'}</td>
                    <td className="py-1 pr-3 font-mono text-blue-300">{e.action}</td>
                    <td className={`py-1 pr-3 font-medium ${
                      e.result?.includes('ok') || e.result?.includes('success')
                        ? 'text-green-400' : 'text-gray-300'
                    }`}>{e.result}</td>
                    <td className="py-1 text-gray-500 truncate max-w-xs">{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
