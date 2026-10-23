/**
 * components/Topology.tsx — Live cluster topology using React Flow.
 *
 * Services are rendered as group nodes containing pod cells.
 * Edges represent call graph connections, coloured by error rate.
 * Fault badges, phase colours, and action indicators animate live.
 */

import React, { useMemo, useCallback } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  Handle,
  Position,
  BackgroundVariant,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useClusterStore } from '../state/clusterStore';
import { useIncidentStore } from '../state/incidentStore';
import type { ServiceMetrics } from '../types/ws';

// ── Colour helpers ────────────────────────────────────────────────────────────

function errorToColor(errorRate: number): string {
  if (errorRate > 0.1) return '#ef4444'; // red
  if (errorRate > 0.02) return '#f59e0b'; // amber
  return '#22c55e'; // green
}

function cpuToColor(cpu: number): string {
  if (cpu > 0.9) return '#ef4444';
  if (cpu > 0.7) return '#f59e0b';
  return '#22c55e';
}

function errorToEdgeColor(errorRate: number): string {
  if (errorRate > 0.1) return '#ef4444';
  if (errorRate > 0.02) return '#f59e0b';
  return '#4b5563';
}

// ── Service node ──────────────────────────────────────────────────────────────

interface ServiceNodeData extends Record<string, unknown> {
  label: string;
  metrics: ServiceMetrics | null;
  hasIncident: boolean;
  hasFault: boolean;
  pods: { id: string; phase: string; cpu: number; mem: number }[];
}

function ServiceNode({ data }: { data: ServiceNodeData }) {
  const m = data.metrics;
  const borderColor = m
    ? errorToColor(m.error_rate)
    : '#4b5563';

  return (
    <div
      style={{ borderColor }}
      className="rounded-lg border-2 bg-gray-900 p-2 min-w-[140px] shadow-lg"
    >
      <Handle type="target" position={Position.Left} style={{ background: '#6b7280' }} />
      <Handle type="source" position={Position.Right} style={{ background: '#6b7280' }} />

      {/* Service header */}
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-gray-200 capitalize">{data.label}</span>
        <div className="flex gap-1">
          {data.hasFault && (
            <span className="text-xs bg-red-900 text-red-300 px-1 rounded" title="Fault active">
              F
            </span>
          )}
          {data.hasIncident && (
            <span className="text-xs bg-amber-900 text-amber-300 px-1 rounded" title="Incident open">
              !
            </span>
          )}
        </div>
      </div>

      {/* Metrics mini-row */}
      {m && (
        <div className="grid grid-cols-2 gap-x-2 text-xs mb-2">
          <span className="text-gray-500">CPU</span>
          <span style={{ color: cpuToColor(m.cpu) }}>{(m.cpu * 100).toFixed(0)}%</span>
          <span className="text-gray-500">Err</span>
          <span style={{ color: errorToColor(m.error_rate) }}>
            {(m.error_rate * 100).toFixed(1)}%
          </span>
          <span className="text-gray-500">p95</span>
          <span className="text-gray-300">{m.latency_p95.toFixed(0)}ms</span>
          <span className="text-gray-500">RPS</span>
          <span className="text-gray-300">{m.request_rate.toFixed(0)}</span>
        </div>
      )}

      {/* Pod cells */}
      <div className="flex flex-wrap gap-1">
        {data.pods.map((pod) => {
          const phase = pod.phase ?? 'Running';
          let bg = 'bg-green-800';
          if (phase === 'CrashLoopBackOff' || phase === 'OOMKilled') bg = 'bg-red-800';
          else if (phase === 'Pending' || phase === 'Unknown') bg = 'bg-amber-800';
          else if (phase === 'Terminating') bg = 'bg-gray-700';
          return (
            <div
              key={pod.id}
              className={`w-3 h-3 rounded-sm ${bg} opacity-90`}
              title={`${pod.id}: ${phase}`}
            />
          );
        })}
        {data.pods.length === 0 && (
          <span className="text-xs text-gray-600">no pods</span>
        )}
      </div>

      {/* Replica count */}
      {m && (
        <div className="mt-1 text-xs text-gray-500">
          {m.replicas ?? 0} replica{(m.replicas ?? 0) !== 1 ? 's' : ''}
        </div>
      )}
    </div>
  );
}

const nodeTypes = { service: ServiceNode };

// ── Static layout positions ───────────────────────────────────────────────────
// Shop topology: frontend → catalog, cart, checkout → payment, cart ← checkout; catalog → cache

const STATIC_POSITIONS: Record<string, { x: number; y: number }> = {
  frontend:  { x: 20,  y: 160 },
  catalog:   { x: 260, y: 40  },
  cart:      { x: 260, y: 200 },
  checkout:  { x: 260, y: 360 },
  payment:   { x: 500, y: 300 },
  cache:     { x: 500, y: 40  },
};

// Static call graph edges (source → target)
const CALL_GRAPH: [string, string][] = [
  ['frontend', 'catalog'],
  ['frontend', 'cart'],
  ['frontend', 'checkout'],
  ['checkout', 'payment'],
  ['checkout', 'cart'],
  ['catalog',  'cache'],
];

// ── Main topology component ───────────────────────────────────────────────────

interface TopologyProps {
  /** Optional: show in compact mode (no minimap) */
  compact?: boolean;
}

export default function Topology({ compact = false }: TopologyProps) {
  const { services, activeFaults } = useClusterStore();
  const openIncidentServices = useClusterStore((s) => s.openIncidentServices);
  const { incidents } = useIncidentStore();

  // Build pod-level data from incident actions (for pod phase simulation)
  const podsByService: Record<string, ServiceNodeData['pods']> = useMemo(() => {
    const result: Record<string, ServiceNodeData['pods']> = {};
    for (const [svcName, m] of Object.entries(services)) {
      const replicas = m.replicas ?? 2;
      result[svcName] = Array.from({ length: replicas }, (_, i) => ({
        id: `${svcName}-${i}`,
        phase: 'Running',
        cpu: m.cpu / replicas,
        mem: m.memory / replicas,
      }));
    }
    return result;
  }, [services]);

  const faultTargets = useMemo(() => {
    const s = new Set<string>();
    for (const f of Object.values(activeFaults)) s.add(f.target);
    return s;
  }, [activeFaults]);

  // Build React Flow nodes
  const initialNodes: Node[] = useMemo(() =>
    Object.keys(STATIC_POSITIONS).map((svcName) => ({
      id: svcName,
      type: 'service',
      position: STATIC_POSITIONS[svcName],
      data: {
        label: svcName,
        metrics: services[svcName] ?? null,
        hasIncident: openIncidentServices.includes(svcName),
        hasFault: faultTargets.has(svcName),
        pods: podsByService[svcName] ?? [],
      } as ServiceNodeData,
    })),
    [services, openIncidentServices, faultTargets, podsByService]
  );

  // Build React Flow edges from call graph
  const initialEdges: Edge[] = useMemo(() =>
    CALL_GRAPH.map(([source, target]) => {
      const srcMetrics = services[source];
      const tgtMetrics = services[target];
      const errorRate = Math.max(
        srcMetrics?.error_rate ?? 0,
        tgtMetrics?.error_rate ?? 0
      );
      const requestRate = srcMetrics?.request_rate ?? 0;
      return {
        id: `${source}-${target}`,
        source,
        target,
        style: {
          stroke: errorToEdgeColor(errorRate),
          strokeWidth: Math.max(1, Math.min(4, requestRate / 20)),
          opacity: 0.8,
        },
        animated: errorRate > 0.05,
        label: requestRate > 0 ? `${requestRate.toFixed(0)} rps` : undefined,
        labelStyle: { fill: '#9ca3af', fontSize: 9 },
        labelBgStyle: { fill: '#111827', fillOpacity: 0.8 },
      };
    }),
    [services]
  );

  const [nodes, , onNodesChange] = useNodesState(initialNodes);
  const [edges, , onEdgesChange] = useEdgesState(initialEdges);

  // Sync nodes with live data (can't use initialNodes directly due to useNodesState)
  const liveNodes = useMemo(() =>
    nodes.map((n) => ({
      ...n,
      data: {
        label: n.id,
        metrics: services[n.id] ?? null,
        hasIncident: openIncidentServices.includes(n.id),
        hasFault: faultTargets.has(n.id),
        pods: podsByService[n.id] ?? [],
      } as ServiceNodeData,
    })),
    [nodes, services, openIncidentServices, faultTargets, podsByService]
  );

  const liveEdges = useMemo(() =>
    edges.map((e) => {
      const srcMetrics = services[e.source];
      const errorRate = srcMetrics?.error_rate ?? 0;
      const requestRate = srcMetrics?.request_rate ?? 0;
      return {
        ...e,
        style: {
          stroke: errorToEdgeColor(errorRate),
          strokeWidth: Math.max(1, Math.min(4, requestRate / 20)),
          opacity: 0.8,
        },
        animated: errorRate > 0.05,
      };
    }),
    [edges, services]
  );

  return (
    <div className={`bg-gray-950 rounded-lg border border-gray-700 ${compact ? 'h-72' : 'h-[480px]'}`}>
      <ReactFlow
        nodes={liveNodes}
        edges={liveEdges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.3}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#1f2937" />
        <Controls style={{ background: '#1f2937', border: '1px solid #374151' }} />
        {!compact && (
          <MiniMap
            style={{ background: '#111827' }}
            nodeColor={(n) => {
              const d = n.data as ServiceNodeData;
              if (d.hasIncident) return '#f59e0b';
              if (d.hasFault) return '#ef4444';
              return '#22c55e';
            }}
          />
        )}
      </ReactFlow>
    </div>
  );
}
