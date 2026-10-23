/**
 * pages/MissionControl.tsx — Home page / overview dashboard.
 *
 * Shows:
 *   - Health score tile, node/pod/incident counts, automation rate
 *   - Active faults list
 *   - Live narration feed
 *   - Open incidents list
 *   - Service health grid
 */

import React from 'react';
import { useClusterStore } from '../state/clusterStore';
import { useIncidentStore } from '../state/incidentStore';
import { useSimStore } from '../state/simStore';
import Topology from '../components/Topology';

// ── Colour helpers ─────────────────────────────────────────────────────────────

function healthColor(errorRate: number, cpu: number): string {
  if (errorRate > 0.1 || cpu > 0.9) return 'text-red-400';
  if (errorRate > 0.02 || cpu > 0.7) return 'text-amber-400';
  return 'text-green-400';
}

function healthBg(errorRate: number, cpu: number): string {
  if (errorRate > 0.1 || cpu > 0.9) return 'bg-red-900/40 border-red-700';
  if (errorRate > 0.02 || cpu > 0.7) return 'bg-amber-900/40 border-amber-700';
  return 'bg-green-900/20 border-green-800';
}

function computeHealthScore(
  services: Record<string, { error_rate: number; cpu: number }>,
  openCount: number
): number {
  if (Object.keys(services).length === 0) return 100;
  let penalty = 0;
  for (const svc of Object.values(services)) {
    penalty += svc.error_rate * 40;
    if (svc.cpu > 0.9) penalty += 20;
    else if (svc.cpu > 0.7) penalty += 10;
  }
  penalty += openCount * 15;
  return Math.max(0, Math.round(100 - penalty));
}

// ── Tile component ─────────────────────────────────────────────────────────────

function Tile({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 flex flex-col gap-1">
      <span className="text-xs text-gray-400 uppercase tracking-wider">{label}</span>
      <span className={`text-2xl font-bold ${accent ?? 'text-gray-100'}`}>{value}</span>
      {sub && <span className="text-xs text-gray-500">{sub}</span>}
    </div>
  );
}

// ── Narration level badge ──────────────────────────────────────────────────────

const LEVEL_STYLE = {
  info: 'text-gray-400',
  warn: 'text-amber-400',
  error: 'text-red-400',
  success: 'text-green-400',
};

const LEVEL_ICON = {
  info: 'ℹ',
  warn: '⚠',
  error: '✕',
  success: '✓',
};

function formatTime(s: number) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function MissionControl() {
  const { services, nodes, activeFaults, narration, openIncidentServices } =
    useClusterStore();
  const { openIncidents, recentIncidents } = useIncidentStore();
  const { status, speed } = useSimStore();

  const openList = openIncidents();
  const recentList = recentIncidents(10);
  const healthScore = computeHealthScore(services, openList.length);

  const nodeCount = Object.keys(nodes).length;
  const podCount = Object.values(services).reduce((s, svc) => s + (svc.replicas ?? 0), 0);
  const faultList = Object.values(activeFaults);

  const resolved = recentList.filter((i) => i.status === 'RESOLVED');
  const autoHealed = resolved.filter((i) => i.gate_decision === 'AUTO').length;
  const automationRate =
    resolved.length > 0 ? Math.round((autoHealed / resolved.length) * 100) : 0;

  const avgMttr =
    resolved.length > 0 && resolved.some((i) => i.time_to_recover)
      ? Math.round(
          resolved
            .filter((i) => i.time_to_recover)
            .reduce((s, i) => s + (i.time_to_recover ?? 0), 0) /
            resolved.filter((i) => i.time_to_recover).length
        )
      : null;

  const scoreColor =
    healthScore >= 80 ? 'text-green-400' : healthScore >= 50 ? 'text-amber-400' : 'text-red-400';

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-100">Mission Control</h1>
        <span
          className={`text-xs px-2 py-1 rounded ${
            status === 'running'
              ? 'bg-green-900 text-green-300'
              : 'bg-gray-700 text-gray-400'
          }`}
        >
          {status === 'running' ? `Running at ${speed}x` : status}
        </span>
      </div>

      {/* KPI tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
        <Tile
          label="Health Score"
          value={`${healthScore}%`}
          accent={scoreColor}
          sub={openList.length > 0 ? `${openList.length} open incident${openList.length > 1 ? 's' : ''}` : 'All clear'}
        />
        <Tile
          label="Nodes"
          value={nodeCount}
          accent={
            Object.values(nodes).some((n) => n.status !== 'Ready')
              ? 'text-amber-400'
              : 'text-gray-100'
          }
          sub={`${Object.values(nodes).filter((n) => n.status === 'Ready').length} ready`}
        />
        <Tile label="Pods" value={podCount} sub={`${Object.keys(services).length} services`} />
        <Tile
          label="Open Incidents"
          value={openList.length}
          accent={openList.length > 0 ? 'text-amber-400' : 'text-green-400'}
          sub="active now"
        />
        <Tile
          label="Auto-healed"
          value={`${automationRate}%`}
          accent={automationRate >= 70 ? 'text-teal-400' : 'text-gray-300'}
          sub={`${resolved.length} resolved`}
        />
        <Tile
          label="Avg MTTR"
          value={avgMttr !== null ? `${avgMttr}s` : '—'}
          accent="text-blue-300"
          sub="sim-seconds"
        />
      </div>

      {/* Topology view */}
      <div>
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">
          Cluster Topology
        </h2>
        <Topology compact />
      </div>

      {/* Body: 3-column layout on large screens */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Service health grid */}
        <div className="lg:col-span-2 space-y-3">
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
            Service Health
          </h2>
          {Object.keys(services).length === 0 ? (
            <p className="text-gray-600 text-sm">Waiting for telemetry…</p>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              {Object.entries(services).map(([name, svc]) => {
                const hasIncident = openIncidentServices.includes(name);
                return (
                  <div
                    key={name}
                    className={`rounded-lg border p-3 ${healthBg(svc.error_rate, svc.cpu)}`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium text-gray-200 capitalize">{name}</span>
                      {hasIncident && (
                        <span className="text-xs bg-amber-800 text-amber-200 px-1.5 py-0.5 rounded">
                          INC
                        </span>
                      )}
                    </div>
                    <div className="space-y-0.5 text-xs">
                      <div className="flex justify-between">
                        <span className="text-gray-500">CPU</span>
                        <span className={healthColor(0, svc.cpu)}>
                          {(svc.cpu * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Errors</span>
                        <span className={healthColor(svc.error_rate, 0)}>
                          {(svc.error_rate * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Latency p95</span>
                        <span className="text-gray-300">
                          {svc.latency_p95.toFixed(0)}ms
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-gray-500">Replicas</span>
                        <span className="text-gray-300">{svc.replicas ?? '—'}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Active faults */}
          {faultList.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">
                Active Faults
              </h2>
              <div className="space-y-1">
                {faultList.map((f) => (
                  <div
                    key={f.fault_id}
                    className="flex items-center gap-2 text-xs bg-red-900/30 border border-red-800 rounded px-3 py-1.5"
                  >
                    <span className="text-red-400 font-mono">{f.fault_type}</span>
                    <span className="text-gray-400">on</span>
                    <span className="text-gray-300">{f.target}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Open incidents */}
          {openList.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">
                Open Incidents
              </h2>
              <div className="space-y-1.5">
                {openList.map((inc) => (
                  <div
                    key={inc.incident_id}
                    className="flex items-center justify-between text-xs bg-amber-900/20 border border-amber-800 rounded px-3 py-2"
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-amber-400 font-medium capitalize">{inc.service}</span>
                      <span className="text-gray-500 font-mono">{inc.incident_id}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      {inc.diagnosis && (
                        <span className="text-teal-300">{inc.diagnosis.label}</span>
                      )}
                      {inc.gate_decision && (
                        <span
                          className={`px-1.5 py-0.5 rounded font-medium ${
                            inc.gate_decision === 'AUTO'
                              ? 'bg-blue-900 text-blue-200'
                              : inc.gate_decision === 'ASK'
                              ? 'bg-purple-900 text-purple-200'
                              : 'bg-red-900 text-red-200'
                          }`}
                        >
                          {inc.gate_decision}
                        </span>
                      )}
                      <span className="text-gray-500">{inc.status}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Narration feed */}
        <div>
          <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Live Narration
          </h2>
          <div className="bg-gray-900 border border-gray-700 rounded-lg h-[480px] overflow-y-auto p-3 space-y-1.5 font-mono text-xs">
            {narration.length === 0 ? (
              <span className="text-gray-600">Waiting for events…</span>
            ) : (
              narration.map((entry) => (
                <div key={entry.id} className="flex gap-2">
                  <span className="text-gray-600 shrink-0">{formatTime(entry.sim_time)}</span>
                  <span className={LEVEL_ICON[entry.level] ? LEVEL_STYLE[entry.level] : 'text-gray-400'}>
                    {LEVEL_ICON[entry.level] ?? '·'}
                  </span>
                  <span className="text-gray-300 leading-relaxed">{entry.text}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
