/**
 * pages/CompareLab.tsx — Lockstep comparison of A0/A1/A2 healing arms.
 *
 * Start a compare run with a fault schedule, poll for results,
 * show MTTR table + per-arm metric charts.
 */

import React, { useState, useEffect, useRef } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { requestJson } from '../api/request';

// ── Types ─────────────────────────────────────────────────────────────────────

interface FaultScheduleEntry {
  kind: string;
  target: string;
  at: number;
  duration: number;
  intensity: number;
}

interface CompareRunStatus {
  run_id: string;
  status: 'pending' | 'running' | 'done' | 'error';
  error?: string;
  seed?: number;
  duration?: number;
  arms?: Record<string, ArmSummary>;
}

interface ArmSummary {
  mode: string;
  mttr_seconds: number | null;
  resolved_count: number;
  escalated_count: number;
  unsafe_actions: number;
  total_actions: number;
  incident_count: number;
  elapsed_wall_seconds: number;
}

// ── Arm colours ───────────────────────────────────────────────────────────────

const ARM_COLORS: Record<string, string> = {
  k8s_defaults:   '#6b7280',
  rule_based:     '#f59e0b',
  full_trustheal: '#22c55e',
};

const ARM_LABELS: Record<string, string> = {
  k8s_defaults:   'A0: K8s Defaults',
  rule_based:     'A1: Rule-based',
  full_trustheal: 'A2: TrustHeal',
};

// ── Fault schedule presets ────────────────────────────────────────────────────

const PRESET_SCHEDULES: Record<string, FaultScheduleEntry[]> = {
  'Memory + CPU stress': [
    { kind: 'memory_exhaustion', target: 'checkout', at: 120, duration: 300, intensity: 1.0 },
    { kind: 'cpu_stress',        target: 'frontend', at: 180, duration: 300, intensity: 1.0 },
  ],
  'Bad deploy + Traffic surge': [
    { kind: 'bad_deploy',    target: 'payment',  at: 90,  duration: 400, intensity: 1.0 },
    { kind: 'traffic_surge', target: 'frontend', at: 200, duration: 300, intensity: 1.5 },
  ],
  'Node failure': [
    { kind: 'cpu_stress',   target: 'frontend',  at: 60,  duration: 200, intensity: 0.8 },
    { kind: 'node_failure', target: 'node-1',    at: 150, duration: 300, intensity: 1.0 },
  ],
};

// ── Summary table ─────────────────────────────────────────────────────────────

function SummaryTable({ arms }: { arms: Record<string, ArmSummary> }) {
  const armNames = Object.keys(arms);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-gray-500 border-b border-gray-700">
            <th className="text-left py-2 pr-4">Arm</th>
            <th className="text-right py-2 pr-4">MTTR (sim-s)</th>
            <th className="text-right py-2 pr-4">Resolved</th>
            <th className="text-right py-2 pr-4">Escalated</th>
            <th className="text-right py-2 pr-4">Actions</th>
            <th className="text-right py-2 pr-4">Unsafe Actions</th>
            <th className="text-right py-2">Wall (s)</th>
          </tr>
        </thead>
        <tbody>
          {armNames.map((arm) => {
            const a = arms[arm];
            const isBest = a.mttr_seconds !== null &&
              armNames.every((other) =>
                arms[other].mttr_seconds === null || arms[other].mttr_seconds! >= a.mttr_seconds!
              );
            return (
              <tr key={arm} className="border-b border-gray-800">
                <td className="py-2 pr-4">
                  <div className="flex items-center gap-2">
                    <div
                      className="w-3 h-3 rounded-full shrink-0"
                      style={{ background: ARM_COLORS[arm] }}
                    />
                    <span className="font-medium" style={{ color: ARM_COLORS[arm] }}>
                      {ARM_LABELS[arm] ?? arm}
                    </span>
                  </div>
                </td>
                <td className={`py-2 pr-4 text-right font-bold ${
                  isBest ? 'text-green-400' : 'text-gray-300'
                }`}>
                  {a.mttr_seconds !== null ? a.mttr_seconds.toFixed(0) : '—'}
                  {isBest && ' ⭐'}
                </td>
                <td className="py-2 pr-4 text-right text-gray-300">{a.resolved_count}</td>
                <td className="py-2 pr-4 text-right text-gray-400">{a.escalated_count}</td>
                <td className="py-2 pr-4 text-right text-gray-300">{a.total_actions}</td>
                <td className={`py-2 pr-4 text-right font-semibold ${
                  a.unsafe_actions === 0 ? 'text-green-400' : 'text-red-400'
                }`}>
                  {a.unsafe_actions}
                </td>
                <td className="py-2 text-right text-gray-500">
                  {a.elapsed_wall_seconds.toFixed(1)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Metric chart (all arms overlaid) ─────────────────────────────────────────

interface MetricPoint { sim_time: number; value: number }

function ArmMetricChart({
  runId,
  service,
  metric,
}: {
  runId: string;
  service: string;
  metric: string;
}) {
  const [data, setData] = useState<Record<string, MetricPoint[]>>({});
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    setError('');
    void requestJson<Record<string, MetricPoint[]>>(
      `/api/compare/${runId}/metrics?service=${encodeURIComponent(service)}&metric=${encodeURIComponent(metric)}`
    )
      .then((result) => { if (active) setData(result); })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : 'Unable to load metric series');
      });
    return () => { active = false; };
  }, [runId, service, metric]);

  // Merge all arms into one array for recharts
  const times = new Set<number>();
  for (const series of Object.values(data)) {
    for (const p of series) times.add(p.sim_time);
  }
  const merged = Array.from(times).sort((a, b) => a - b).map((t) => {
    const row: Record<string, number> = { sim_time: t };
    for (const [arm, series] of Object.entries(data)) {
      const pt = series.find((p) => p.sim_time === t);
      if (pt) row[arm] = pt.value;
    }
    return row;
  });

  const armNames = Object.keys(data);

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-3">
      <div className="text-xs text-gray-400 mb-2 capitalize">
        {service} / {metric.replace('_', ' ')}
      </div>
      {error ? (
        <p className="py-6 text-xs text-red-400" role="alert">{error}</p>
      ) : (
        <ResponsiveContainer width="100%" height={160}>
          <LineChart data={merged} margin={{ top: 2, right: 8, bottom: 2, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis
            dataKey="sim_time"
            tick={{ fontSize: 9, fill: '#6b7280' }}
            tickFormatter={(t) => `${Math.floor(Number(t) / 60)}m`}
          />
          <YAxis tick={{ fontSize: 9, fill: '#6b7280' }} width={32} />
          <Tooltip
            contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 11 }}
            labelFormatter={(t) => `t=${t}s`}
          />
          <Legend
            wrapperStyle={{ fontSize: 10 }}
            formatter={(v) => ARM_LABELS[v] ?? v}
          />
          {armNames.map((arm) => (
            <Line
              key={arm}
              type="monotone"
              dataKey={arm}
              stroke={ARM_COLORS[arm] ?? '#9ca3af'}
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function CompareLab() {
  const [preset, setPreset] = useState('Memory + CPU stress');
  const [seed, setSeed] = useState(42);
  const [duration, setDuration] = useState(900);
  const [runId, setRunId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<CompareRunStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const startRun = async () => {
    setStarting(true);
    setError('');
    setRunId(null);
    setRunStatus(null);
    try {
      const d = await requestJson<CompareRunStatus>('/api/compare/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          seed,
          duration_sim_seconds: duration,
          fault_schedule: PRESET_SCHEDULES[preset] ?? [],
        }),
      });
      setRunId(d.run_id);
      setRunStatus(d);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Failed to start compare run');
    } finally {
      setStarting(false);
    }
  };

  // Poll for completion
  useEffect(() => {
    if (!runId) return;
    if (runStatus?.status === 'done' || runStatus?.status === 'error') return;

    const poll = async () => {
      try {
        const result = await requestJson<CompareRunStatus>(`/api/compare/${runId}`);
        setRunStatus(result);
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : 'Unable to load comparison status';
        setRunStatus((current) => current ? { ...current, status: 'error', error: message } : current);
      }
    };

    pollTimer.current = setInterval(poll, 1000);
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, [runId, runStatus?.status]);

  const done = runStatus?.status === 'done';

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-xl font-semibold text-gray-100">Compare Lab</h1>
      <p className="text-xs text-gray-500">
        Run the same fault scenario in three arms (A0: K8s defaults, A1: Rule-based, A2: Full TrustHeal)
        with the same seed and fault schedule, then compare MTTR and unsafe actions.
      </p>

      {/* Config */}
      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">Configure Run</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
          <div>
            <label className="text-xs text-gray-500 block mb-1">Fault preset</label>
            <select
              value={preset}
              onChange={(e) => setPreset(e.target.value)}
              className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-gray-200"
            >
              {Object.keys(PRESET_SCHEDULES).map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Seed</label>
            <input
              type="number"
              value={seed}
              onChange={(e) => setSeed(parseInt(e.target.value))}
              className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-gray-200"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Duration (sim-s)</label>
            <select
              value={duration}
              onChange={(e) => setDuration(parseInt(e.target.value))}
              className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-gray-200"
            >
              {[300, 600, 900, 1800].map((d) => (
                <option key={d} value={d}>{d}s ({Math.round(d / 60)}min)</option>
              ))}
            </select>
          </div>
        </div>

        {/* Fault schedule preview */}
        <div className="mb-3">
          <span className="text-xs text-gray-500">Faults: </span>
          {(PRESET_SCHEDULES[preset] ?? []).map((f, i) => (
            <span key={i} className="text-xs text-red-300 mr-2">
              {f.kind} on {f.target} at t={f.at}s
            </span>
          ))}
        </div>

        <button
          onClick={startRun}
          disabled={starting}
          className="px-5 py-2 rounded bg-teal-800 hover:bg-teal-700 text-teal-100 text-sm font-semibold disabled:opacity-50"
        >
          {starting ? 'Starting…' : 'Run Comparison'}
        </button>
        {error && <p className="text-red-400 text-xs mt-2">{error}</p>}
      </div>

      {/* Status indicator */}
      {runStatus && runStatus.status !== 'done' && (
        <div className="bg-blue-900/20 border border-blue-800 rounded p-3 text-sm">
          {runStatus.status === 'running' ? (
            <span className="text-blue-300">Running comparison arms in parallel…</span>
          ) : runStatus.status === 'error' ? (
            <span className="text-red-400">Error: {runStatus.error}</span>
          ) : (
            <span className="text-gray-400">Starting…</span>
          )}
        </div>
      )}

      {/* Results */}
      {done && runStatus?.arms && (
        <>
          <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
            <h2 className="text-sm font-semibold text-gray-400 mb-3">Results Summary</h2>
            <SummaryTable arms={runStatus.arms} />
          </div>

          {/* Metric charts */}
          <div>
            <h2 className="text-sm font-semibold text-gray-400 mb-3">Metric Comparison</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {['error_rate', 'cpu', 'latency_p95', 'request_rate'].map((metric) => (
                <ArmMetricChart
                  key={metric}
                  runId={runStatus.run_id}
                  service="frontend"
                  metric={metric}
                />
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
