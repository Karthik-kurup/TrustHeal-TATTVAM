/**
 * pages/MetricsExplorer.tsx — Live metrics charts per service.
 *
 * Shows CPU, memory, latency p95, error rate, request rate, restarts.
 * Baseline band + vertical markers for fault onset, incident open, action.
 * Data comes from GET /api/metrics/query (historical) + WS tick (live).
 */

import React, { useState, useEffect, useRef } from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  ReferenceArea,
} from 'recharts';
import { useClusterStore } from '../state/clusterStore';
import { useIncidentStore } from '../state/incidentStore';
import { useSimStore } from '../state/simStore';
import type { ServiceMetrics } from '../types/ws';

// ── Types ─────────────────────────────────────────────────────────────────────

interface MetricPoint {
  sim_time: number;
  value: number;
}

type MetricKey = 'cpu' | 'memory' | 'latency_p95' | 'error_rate' | 'request_rate' | 'restarts';

const METRIC_LABELS: Record<MetricKey, string> = {
  cpu: 'CPU Util (%)',
  memory: 'Memory (%)',
  latency_p95: 'Latency p95 (ms)',
  error_rate: 'Error Rate (%)',
  request_rate: 'Request Rate (rps)',
  restarts: 'Restarts',
};

const METRIC_COLORS: Record<MetricKey, string> = {
  cpu: '#3b82f6',
  memory: '#8b5cf6',
  latency_p95: '#f59e0b',
  error_rate: '#ef4444',
  request_rate: '#22c55e',
  restarts: '#e879f9',
};

// Scale a raw ServiceMetrics value to display units
function scaleValue(key: MetricKey, m: ServiceMetrics): number {
  switch (key) {
    case 'cpu': return m.cpu * 100;
    case 'memory': return m.memory * 100;
    case 'latency_p95': return m.latency_p95;
    case 'error_rate': return m.error_rate * 100;
    case 'request_rate': return m.request_rate;
    case 'restarts': return m.restarts;
    default: return 0;
  }
}

const BACKEND_METRIC_KEY: Record<MetricKey, string> = {
  cpu: 'cpu_util',
  memory: 'mem_ratio',
  latency_p95: 'latency_p95',
  error_rate: 'error_rate',
  request_rate: 'request_rate',
  restarts: 'restarts',
};

// ── Single metric chart ───────────────────────────────────────────────────────

interface MetricChartProps {
  metricKey: MetricKey;
  data: MetricPoint[];
  incidentTimes: number[];
  faultTimes: number[];
  actionTimes: number[];
  baseline: number | null;
}

function MetricChart({ metricKey, data, incidentTimes, faultTimes, actionTimes, baseline }: MetricChartProps) {
  const color = METRIC_COLORS[metricKey];
  const label = METRIC_LABELS[metricKey];

  const formatter = (val: number) => {
    if (metricKey === 'latency_p95') return `${val.toFixed(0)}ms`;
    if (metricKey === 'request_rate') return `${val.toFixed(0)}rps`;
    if (metricKey === 'restarts') return String(Math.round(val));
    return `${val.toFixed(1)}%`;
  };

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-3">
      <div className="text-xs text-gray-400 mb-2">{label}</div>
      <ResponsiveContainer width="100%" height={120}>
        <LineChart data={data} margin={{ top: 2, right: 8, bottom: 2, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis
            dataKey="sim_time"
            tick={{ fontSize: 9, fill: '#6b7280' }}
            tickFormatter={(t) => `${Math.floor(t / 60)}m`}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 9, fill: '#6b7280' }}
            tickLine={false}
            axisLine={false}
            width={32}
            tickFormatter={formatter}
          />
          <Tooltip
            contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 11 }}
            labelFormatter={(t) => `t=${Math.floor(Number(t))}s`}
            formatter={(val: unknown) => [formatter(Number(val)), label]}
          />

          {/* Baseline band */}
          {baseline !== null && (
            <ReferenceArea
              y1={baseline * 0.85}
              y2={baseline * 1.15}
              fill="#22c55e"
              fillOpacity={0.05}
            />
          )}

          {/* Fault onset markers */}
          {faultTimes.map((t) => (
            <ReferenceLine key={`f-${t}`} x={t} stroke="#ef4444" strokeDasharray="4 2" strokeWidth={1} />
          ))}

          {/* Incident open markers */}
          {incidentTimes.map((t) => (
            <ReferenceLine key={`i-${t}`} x={t} stroke="#f59e0b" strokeDasharray="4 2" strokeWidth={1} />
          ))}

          {/* Action markers */}
          {actionTimes.map((t) => (
            <ReferenceLine key={`a-${t}`} x={t} stroke="#3b82f6" strokeDasharray="4 2" strokeWidth={1} />
          ))}

          <Line
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── Service panel ─────────────────────────────────────────────────────────────

const METRICS_TO_SHOW: MetricKey[] = [
  'cpu', 'memory', 'latency_p95', 'error_rate', 'request_rate', 'restarts',
];

const MAX_LIVE_POINTS = 120; // keep 120 ticks = 30 min at 15s/tick

interface ServicePanelProps {
  serviceName: string;
}

function ServicePanel({ serviceName }: ServicePanelProps) {
  const liveMetrics = useClusterStore((s) => s.services[serviceName]);
  const simTime = useSimStore((s) => s.simTime);
  const incidents = useIncidentStore((s) => s.recentIncidents(20));
  const activeFaults = useClusterStore((s) => s.activeFaults);

  // Ring-buffer of live points
  const liveBuffers = useRef<Record<MetricKey, MetricPoint[]>>({
    cpu: [], memory: [], latency_p95: [], error_rate: [], request_rate: [], restarts: [],
  });
  const [chartData, setChartData] = useState<Record<MetricKey, MetricPoint[]>>({
    cpu: [], memory: [], latency_p95: [], error_rate: [], request_rate: [], restarts: [],
  });

  // On each tick push to live buffer
  useEffect(() => {
    if (!liveMetrics) return;
    const buffers = liveBuffers.current;
    for (const key of METRICS_TO_SHOW) {
      const val = scaleValue(key, liveMetrics);
      buffers[key] = [...buffers[key], { sim_time: simTime, value: val }].slice(-MAX_LIVE_POINTS);
    }
    setChartData({ ...buffers });
  }, [liveMetrics, simTime]);

  // Collect event markers
  const serviceIncidents = incidents.filter((i) => i.service === serviceName);
  const incidentTimes = serviceIncidents.map((i) => i.opened_at);
  const actionTimes = serviceIncidents.flatMap((i) =>
    i.actions.map((a) => a.executed_at).filter((t): t is number => t !== undefined)
  );
  const faultTimes = Object.values(activeFaults)
    .filter((f) => f.target === serviceName)
    .map(() => simTime); // approx — will be from fault event sim_time in full impl

  // Baseline (first 4 points per metric)
  const baselines: Record<MetricKey, number | null> = {} as Record<MetricKey, number | null>;
  for (const key of METRICS_TO_SHOW) {
    const buf = chartData[key];
    if (buf.length >= 4) {
      baselines[key] = buf.slice(0, 4).reduce((s, p) => s + p.value, 0) / 4;
    } else {
      baselines[key] = null;
    }
  }

  if (!liveMetrics && chartData.cpu.length === 0) {
    return (
      <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-300 capitalize mb-2">{serviceName}</h3>
        <p className="text-xs text-gray-600">No telemetry yet…</p>
      </div>
    );
  }

  return (
    <div className="bg-gray-800/50 border border-gray-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-200 capitalize">{serviceName}</h3>
        {liveMetrics && (
          <div className="flex gap-3 text-xs text-gray-500">
            <span>{(liveMetrics.cpu * 100).toFixed(0)}% CPU</span>
            <span>{(liveMetrics.error_rate * 100).toFixed(1)}% err</span>
            <span>{liveMetrics.replicas ?? '—'} pods</span>
          </div>
        )}
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {METRICS_TO_SHOW.map((key) => (
          <MetricChart
            key={key}
            metricKey={key}
            data={chartData[key]}
            incidentTimes={incidentTimes}
            faultTimes={faultTimes}
            actionTimes={actionTimes}
            baseline={baselines[key]}
          />
        ))}
      </div>
    </div>
  );
}

// ── Legend ────────────────────────────────────────────────────────────────────

function Legend() {
  return (
    <div className="flex items-center gap-4 text-xs text-gray-500">
      <div className="flex items-center gap-1">
        <div className="w-4 border-t border-red-500 border-dashed" />
        <span>Fault onset</span>
      </div>
      <div className="flex items-center gap-1">
        <div className="w-4 border-t border-amber-500 border-dashed" />
        <span>Incident opened</span>
      </div>
      <div className="flex items-center gap-1">
        <div className="w-4 border-t border-blue-500 border-dashed" />
        <span>Action taken</span>
      </div>
      <div className="flex items-center gap-1">
        <div className="w-4 h-2 bg-green-500 opacity-20" />
        <span>Baseline ±15%</span>
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

const ALL_SERVICES = ['frontend', 'catalog', 'cart', 'checkout', 'payment', 'cache'];

export default function MetricsExplorer() {
  const [selected, setSelected] = useState<string[]>(ALL_SERVICES);
  const { services } = useClusterStore();

  const serviceList = Object.keys(services).length > 0 ? Object.keys(services) : ALL_SERVICES;

  const toggleService = (svc: string) => {
    setSelected((prev) =>
      prev.includes(svc) ? prev.filter((s) => s !== svc) : [...prev, svc]
    );
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-100">Metrics Explorer</h1>
        <Legend />
      </div>

      {/* Service filter */}
      <div className="flex flex-wrap gap-2">
        {serviceList.map((svc) => (
          <button
            key={svc}
            onClick={() => toggleService(svc)}
            className={`px-3 py-1 rounded text-xs font-medium transition-colors ${
              selected.includes(svc)
                ? 'bg-blue-800 text-blue-200 border border-blue-600'
                : 'bg-gray-800 text-gray-400 border border-gray-700'
            }`}
          >
            {svc}
          </button>
        ))}
      </div>

      {/* Service panels */}
      <div className="space-y-6">
        {serviceList
          .filter((svc) => selected.includes(svc))
          .map((svc) => (
            <ServicePanel key={svc} serviceName={svc} />
          ))}
      </div>
    </div>
  );
}
