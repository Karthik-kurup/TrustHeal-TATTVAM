/**
 * pages/TwinStudio.tsx — Digital Twin Studio.
 * Fault injection, simulation controls, snapshot/rewind, what-if branching.
 */

import React, { useState, useEffect } from 'react';
import { useSimStore } from '../state/simStore';
import { useClusterStore } from '../state/clusterStore';
import Topology from '../components/Topology';
import { requestJson } from '../api/request';

// ── Helpers ───────────────────────────────────────────────────────────────────

async function postControl(cmd: string, value?: number) {
  await requestJson('/api/sim/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd, value }),
  });
}

// ── Snapshot row ──────────────────────────────────────────────────────────────

interface SnapEntry { id: string; sim_time: number }

function SnapshotPanel() {
  const [snapshots, setSnapshots] = useState<SnapEntry[]>([]);
  const [saving, setSaving] = useState(false);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [message, setMessage] = useState('');

  const refreshSnapshots = async () => {
    const data = await requestJson<SnapEntry[]>('/api/sim/snapshots');
    setSnapshots(data);
  };

  useEffect(() => { refreshSnapshots(); }, []);

  const saveSnapshot = async () => {
    setSaving(true);
    setMessage('');
    try {
      const d = await requestJson<{ snapshot_id: string }>('/api/sim/snapshot', { method: 'POST' });
      setMessage(`Saved: ${d.snapshot_id}`);
      await refreshSnapshots();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to save snapshot');
    } finally {
      setSaving(false);
    }
  };

  const restoreSnapshot = async (id: string) => {
    setRestoring(id);
    setMessage('');
    try {
      await requestJson('/api/sim/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ snapshot_id: id }),
      });
      setMessage(`Restored: ${id}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to restore snapshot');
    } finally {
      setRestoring(null);
    }
  };

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-400">Snapshots &amp; Rewind</h2>
        <button
          onClick={saveSnapshot}
          disabled={saving}
          className="px-3 py-1 rounded bg-blue-800 hover:bg-blue-700 text-blue-100 text-xs font-medium disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save Snapshot'}
        </button>
      </div>
      {message && <p className="text-xs text-teal-400 mb-2">{message}</p>}
      {snapshots.length === 0 ? (
        <p className="text-xs text-gray-600">No snapshots yet.</p>
      ) : (
        <div className="space-y-1">
          {snapshots.map((s) => (
            <div key={s.id} className="flex items-center justify-between text-xs bg-gray-700 rounded px-3 py-1.5">
              <span className="font-mono text-gray-300">{s.id}</span>
              <span className="text-gray-500">t={Math.round(s.sim_time)}s</span>
              <button
                onClick={() => restoreSnapshot(s.id)}
                disabled={restoring === s.id}
                className="text-amber-400 hover:text-amber-300 disabled:opacity-50"
              >
                {restoring === s.id ? 'Restoring…' : 'Restore'}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Scenario panel ────────────────────────────────────────────────────────────

interface ScenarioEntry { id: string; name: string; description: string }

function ScenarioPanel() {
  const [scenarios, setScenarios] = useState<ScenarioEntry[]>([]);
  const [running, setRunning] = useState<string | null>(null);
  const [message, setMessage] = useState('');

  useEffect(() => {
    void requestJson<ScenarioEntry[]>('/api/scenarios')
      .then(setScenarios)
      .catch((error: unknown) => {
        setMessage(error instanceof Error ? error.message : 'Unable to load scenarios');
      });
  }, []);

  const runScenario = async (id: string) => {
    setRunning(id);
    setMessage('');
    try {
      await requestJson(`/api/scenarios/${id}/run`, { method: 'POST' });
      setMessage(`Running scenario: ${id}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `Unable to run scenario: ${id}`);
    } finally {
      setRunning(null);
    }
  };

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
      <h2 className="text-sm font-semibold text-gray-400 mb-3">Scenarios</h2>
      {message && <p className="text-xs text-teal-400 mb-2">{message}</p>}
      {scenarios.length === 0 ? (
        <p className="text-xs text-gray-600">Loading scenarios…</p>
      ) : (
        <div className="space-y-1.5">
          {scenarios.map((s) => (
            <div key={s.id} className="flex items-center justify-between bg-gray-700 rounded px-3 py-2 text-xs">
              <div>
                <div className="text-gray-200 font-medium">{s.name}</div>
                <div className="text-gray-500">{s.description}</div>
              </div>
              <button
                onClick={() => runScenario(s.id)}
                disabled={running === s.id}
                className="ml-3 px-3 py-1 rounded bg-teal-800 hover:bg-teal-700 text-teal-100 disabled:opacity-50"
              >
                {running === s.id ? 'Starting…' : 'Run'}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

const FAULT_TYPES = [
  'cpu_stress', 'traffic_surge', 'memory_exhaustion', 'memory_leak',
  'network_loss', 'bad_deploy', 'node_failure',
] as const;

const SERVICES = ['frontend', 'catalog', 'cart', 'checkout', 'payment', 'cache'] as const;
const NODES = ['node-1', 'node-2', 'node-3', 'node-4'] as const;

export default function TwinStudio() {
  const { status, speed, simTime, seed } = useSimStore();
  const { activeFaults } = useClusterStore();

  const [faultType, setFaultType] = useState<string>('cpu_stress');
  const [faultTarget, setFaultTarget] = useState<string>('frontend');
  const [intensity, setIntensity] = useState(1.0);
  const [duration, setDuration] = useState(300);
  const [injecting, setInjecting] = useState(false);
  const [injectMsg, setInjectMsg] = useState('');

  // For node_failure, target should be a node not a service
  const isNodeFault = faultType === 'node_failure';
  const targetOptions = isNodeFault ? [...NODES] : [...SERVICES];

  const injectFault = async () => {
    setInjecting(true);
    setInjectMsg('');
    try {
      const res = await fetch('/api/faults/inject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          kind: faultType,
          target: faultTarget,
          intensity,
          duration,
        }),
      });
      const data = await res.json() as { fault_id?: string; detail?: string };
      if (res.ok) {
        setInjectMsg(`Injected: ${data.fault_id}`);
      } else {
        setInjectMsg(`Error: ${data.detail ?? 'unknown'}`);
      }
    } catch {
      setInjectMsg('Network error');
    } finally {
      setInjecting(false);
    }
  };

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-100">Digital Twin Studio</h1>
        <div className="text-xs text-gray-500 font-mono">
          seed={seed} · t={Math.round(simTime)}s
        </div>
      </div>

      {/* Live topology */}
      <div>
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-2">
          Live Cluster Topology
        </h2>
        <Topology />
      </div>

      {/* Two-column: Sim controls + Fault injector */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Sim controls */}
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-gray-400 mb-3">Simulation Controls</h2>
          <div className="flex flex-wrap gap-2 text-xs mb-4">
            <button
              onClick={() => postControl(status === 'running' ? 'pause' : 'play')}
              className={`px-3 py-1.5 rounded font-medium ${
                status === 'running'
                  ? 'bg-amber-800 hover:bg-amber-700 text-amber-100'
                  : 'bg-green-800 hover:bg-green-700 text-green-100'
              }`}
            >
              {status === 'running' ? 'Pause' : 'Play'}
            </button>
            <button
              onClick={() => postControl('step')}
              className="px-3 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-200"
            >
              Step
            </button>
            {([1, 5, 10, 30] as const).map((s) => (
              <button
                key={s}
                onClick={() => postControl('speed', s)}
                className={`px-3 py-1.5 rounded ${
                  Math.round(speed) === s
                    ? 'bg-blue-700 text-white'
                    : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
                }`}
              >
                {s}x
              </button>
            ))}
          </div>
          <button
            onClick={() => postControl('reset')}
            className="px-4 py-1.5 rounded bg-red-900/60 hover:bg-red-900 text-red-300 text-xs border border-red-800"
          >
            Reset Simulation
          </button>
        </div>

        {/* Fault injector */}
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-gray-400 mb-3">Fault Injector</h2>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-500 block mb-1">Fault type</label>
                <select
                  value={faultType}
                  onChange={(e) => {
                    setFaultType(e.target.value);
                    setFaultTarget(e.target.value === 'node_failure' ? 'node-1' : 'frontend');
                  }}
                  className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-gray-200"
                >
                  {FAULT_TYPES.map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">Target</label>
                <select
                  value={faultTarget}
                  onChange={(e) => setFaultTarget(e.target.value)}
                  className="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-gray-200"
                >
                  {targetOptions.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-gray-500 block mb-1">
                  Intensity: {intensity.toFixed(1)}
                </label>
                <input
                  type="range" min={0.1} max={2.0} step={0.1}
                  value={intensity}
                  onChange={(e) => setIntensity(parseFloat(e.target.value))}
                  className="w-full"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">
                  Duration: {duration}s
                </label>
                <input
                  type="range" min={30} max={600} step={30}
                  value={duration}
                  onChange={(e) => setDuration(parseInt(e.target.value))}
                  className="w-full"
                />
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={injectFault}
                disabled={injecting}
                className="px-4 py-2 rounded bg-red-800 hover:bg-red-700 text-red-100 text-sm font-medium disabled:opacity-50"
              >
                {injecting ? 'Injecting…' : 'Inject Fault'}
              </button>
              {injectMsg && <span className="text-xs text-gray-400">{injectMsg}</span>}
            </div>
          </div>
        </div>
      </div>

      {/* Active faults */}
      {Object.values(activeFaults).length > 0 && (
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-gray-400 mb-3">Active Faults</h2>
          <div className="space-y-1.5">
            {Object.values(activeFaults).map((f) => (
              <div
                key={f.fault_id}
                className="flex items-center justify-between text-xs bg-red-900/20 border border-red-800 rounded px-3 py-2"
              >
                <div className="flex items-center gap-3">
                  <span className="text-red-400 font-mono font-medium">{f.fault_type}</span>
                  <span className="text-gray-500">on</span>
                  <span className="text-gray-300">{f.target}</span>
                </div>
                <button
                  onClick={async () => {
                    await fetch(`/api/faults/${f.fault_id}`, { method: 'DELETE' });
                  }}
                  className="text-gray-500 hover:text-red-400 text-xs"
                >
                  clear
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Snapshot / rewind */}
      <SnapshotPanel />

      {/* Scenarios */}
      <ScenarioPanel />
    </div>
  );
}
