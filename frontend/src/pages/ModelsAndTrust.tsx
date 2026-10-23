/**
 * pages/ModelsAndTrust.tsx — Confusion matrix, calibration, F1 scores, unseen faults.
 */

import React, { useEffect, useState } from 'react';

// ── Types ─────────────────────────────────────────────────────────────────────

interface PerClassMetrics {
  precision: number;
  recall: number;
  f1: number;
  support: number;
}

interface DetectorMetrics {
  auroc?: number;
  threshold?: number;
  n_anomalies?: number;
}

interface ModelReport {
  generated_at?: string;
  elapsed_seconds?: number;
  n_test_samples?: number;
  n_known_samples?: number;
  n_unseen_samples?: number;
  class_labels?: string[];
  confusion_matrix?: number[][];
  per_class?: Record<string, PerClassMetrics>;
  weighted_avg?: { precision: number; recall: number; f1: number };
  macro_avg?: { precision: number; recall: number; f1: number };
  abstain_rate?: number;
  detectors?: Record<string, DetectorMetrics>;
  unseen_fault_results?: {
    total: number;
    escalated: number;
    wrongly_automated: number;
    wrongly_auto_rate: number | null;
    wilson_ci_lower: number | null;
    wilson_ci_upper: number | null;
    n_unseen_samples?: number;
  };
}

// ── Colour helpers ─────────────────────────────────────────────────────────────

function f1Color(f1: number): string {
  if (f1 >= 0.8) return 'text-green-400';
  if (f1 >= 0.6) return 'text-amber-400';
  return 'text-red-400';
}

// ── Confusion matrix ──────────────────────────────────────────────────────────

function ConfusionMatrix({ matrix, labels }: { matrix: number[][]; labels: string[] }) {
  if (!matrix || !labels || matrix.length === 0) return null;

  // Max value for colour scaling
  const maxVal = Math.max(...matrix.flat());

  return (
    <div className="overflow-x-auto">
      <table className="text-xs border-collapse">
        <thead>
          <tr>
            <th className="text-gray-600 pr-2 text-right text-xs">Pred →</th>
            {labels.map((l) => (
              <th key={l} className="px-1 py-0.5 text-gray-500 text-center max-w-[60px] truncate" title={l}>
                {l.replace('_', ' ').slice(0, 6)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={i}>
              <td className="text-gray-500 pr-2 text-right text-xs truncate max-w-[80px]" title={labels[i]}>
                {labels[i].replace('_', ' ').slice(0, 8)}
              </td>
              {row.map((val, j) => {
                const intensity = maxVal > 0 ? val / maxVal : 0;
                const isCorrect = i === j;
                return (
                  <td
                    key={j}
                    className="w-10 h-7 text-center text-xs border border-gray-800"
                    style={{
                      background: isCorrect
                        ? `rgba(34, 197, 94, ${0.1 + intensity * 0.7})`
                        : val > 0
                        ? `rgba(239, 68, 68, ${0.05 + intensity * 0.5})`
                        : 'transparent',
                      color: val > 0 ? (isCorrect ? '#86efac' : '#fca5a5') : '#374151',
                    }}
                    title={`True: ${labels[i]}, Pred: ${labels[j]}, Count: ${val}`}
                  >
                    {val > 0 ? val : '·'}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Per-class metrics table ───────────────────────────────────────────────────

function PerClassTable({ perClass }: { perClass: Record<string, PerClassMetrics> }) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-gray-500 border-b border-gray-700">
          <th className="text-left py-1 pr-4">Class</th>
          <th className="text-right py-1 pr-4">Precision</th>
          <th className="text-right py-1 pr-4">Recall</th>
          <th className="text-right py-1 pr-4">F1</th>
          <th className="text-right py-1">Support</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(perClass).map(([cls, m]) => (
          <tr key={cls} className="border-b border-gray-800">
            <td className="py-1 pr-4 font-mono text-gray-300">{cls}</td>
            <td className="py-1 pr-4 text-right text-gray-300">{m.precision.toFixed(3)}</td>
            <td className="py-1 pr-4 text-right text-gray-300">{m.recall.toFixed(3)}</td>
            <td className={`py-1 pr-4 text-right font-semibold ${f1Color(m.f1)}`}>
              {m.f1.toFixed(3)}
            </td>
            <td className="py-1 text-right text-gray-500">{m.support}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Detector comparison ───────────────────────────────────────────────────────

function DetectorTable({ detectors }: { detectors: Record<string, DetectorMetrics> }) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-gray-500 border-b border-gray-700">
          <th className="text-left py-1 pr-4">Detector</th>
          <th className="text-right py-1 pr-4">AUROC</th>
          <th className="text-right py-1">Threshold</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(detectors).map(([name, m]) => (
          <tr key={name} className="border-b border-gray-800 text-gray-300">
            <td className="py-1 pr-4 font-mono">{name}</td>
            <td className={`py-1 pr-4 text-right font-semibold ${m.auroc ? f1Color(m.auroc) : 'text-gray-500'}`}>
              {m.auroc?.toFixed(3) ?? '—'}
            </td>
            <td className="py-1 text-right text-gray-400">{m.threshold?.toFixed(4) ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Unseen fault results ──────────────────────────────────────────────────────

function UnseenFaultPanel({ data }: { data: NonNullable<ModelReport['unseen_fault_results']> }) {
  const hasSamples = data.total > 0;
  const allEscalated = hasSamples && data.wrongly_automated === 0;
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <span className={`text-2xl font-bold ${
          !hasSamples ? 'text-gray-400' : allEscalated ? 'text-green-400' : 'text-red-400'
        }`}>
          {data.wrongly_auto_rate != null
            ? `${(data.wrongly_auto_rate * 100).toFixed(1)}%`
            : 'Not evaluated'}
        </span>
        <span className="text-xs text-gray-400">wrongly automated (target: 0%)</span>
        {allEscalated && (
          <span className="text-xs bg-green-900 text-green-300 px-2 py-0.5 rounded">PASS</span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 text-xs">
        <div className="bg-gray-700 rounded p-2">
          <div className="text-gray-500">Total unseen</div>
          <div className="text-gray-200 font-bold text-lg">{data.total}</div>
        </div>
        <div className="bg-gray-700 rounded p-2">
          <div className="text-gray-500">Escalated</div>
          <div className="text-green-400 font-bold text-lg">{hasSamples ? data.escalated : '—'}</div>
        </div>
        <div className="bg-gray-700 rounded p-2">
          <div className="text-gray-500">Wrongly automated</div>
          <div className={`font-bold text-lg ${
            !hasSamples ? 'text-gray-400' : data.wrongly_automated === 0 ? 'text-green-400' : 'text-red-400'
          }`}>
            {hasSamples ? data.wrongly_automated : '—'}
          </div>
        </div>
        <div className="bg-gray-700 rounded p-2">
          <div className="text-gray-500">Wilson CI (wrongly-auto rate)</div>
          <div className="text-gray-300 text-sm">
            {data.wilson_ci_lower !== null && data.wilson_ci_upper !== null
              ? `[${(data.wilson_ci_lower * 100).toFixed(1)}%, ${(data.wilson_ci_upper * 100).toFixed(1)}%]`
              : '—'}
          </div>
        </div>
      </div>
      {data.n_unseen_samples === 0 && (
        <p className="text-xs text-gray-500 italic">
          No unseen-fault samples are available, so the safety rate has not been evaluated.
        </p>
      )}
    </div>
  );
}

// ── Model status panel ────────────────────────────────────────────────────────

interface ModelStatus {
  all_present: boolean;
  artefacts: Record<string, boolean>;
}

function ModelStatusPanel() {
  const [status, setStatus] = useState<ModelStatus | null>(null);

  useEffect(() => {
    fetch('/api/models/status')
      .then((r) => r.json())
      .then((d: ModelStatus) => setStatus(d))
      .catch(() => {});
  }, []);

  if (!status) return null;

  return (
    <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
      <h2 className="text-sm font-semibold text-gray-400 mb-3">Model Artefacts</h2>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {Object.entries(status.artefacts).map(([name, present]) => (
          <div
            key={name}
            className={`flex items-center gap-2 text-xs px-2 py-1 rounded border ${
              present
                ? 'border-green-800 bg-green-900/20 text-green-400'
                : 'border-red-800 bg-red-900/20 text-red-400'
            }`}
          >
            <span>{present ? '✓' : '✕'}</span>
            <span className="font-mono">{name}</span>
          </div>
        ))}
      </div>
      {!status.all_present && (
        <p className="text-xs text-amber-400 mt-2">
          Some artefacts are missing. Run <code className="font-mono">make train</code> to generate them.
        </p>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ModelsAndTrust() {
  const [report, setReport] = useState<ModelReport | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/models/report')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: ModelReport) => { setReport(d); setLoading(false); })
      .catch((e: Error) => {
        setError(e.message.includes('404')
          ? 'Report not found — run `make train` first.'
          : `Error loading report: ${e.message}`);
        setLoading(false);
      });
  }, []);

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-100">Models &amp; Trust</h1>
        {report?.generated_at && (
          <span className="text-xs text-gray-500">
            Generated: {new Date(report.generated_at).toLocaleString()}
          </span>
        )}
      </div>

      <ModelStatusPanel />

      {loading && <p className="text-gray-500 text-sm">Loading report…</p>}
      {error && (
        <div className="bg-red-900/20 border border-red-800 rounded p-4 text-sm text-red-400">
          {error}
        </div>
      )}

      {report && (
        <>
          {/* Summary row */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-gray-800 border border-gray-700 rounded-lg p-3">
              <div className="text-xs text-gray-400">Test samples</div>
              <div className="text-2xl font-bold text-gray-100">{report.n_test_samples?.toLocaleString()}</div>
            </div>
            <div className="bg-gray-800 border border-gray-700 rounded-lg p-3">
              <div className="text-xs text-gray-400">Weighted F1</div>
              <div className={`text-2xl font-bold ${f1Color(report.weighted_avg?.f1 ?? 0)}`}>
                {report.weighted_avg?.f1.toFixed(3)}
              </div>
            </div>
            <div className="bg-gray-800 border border-gray-700 rounded-lg p-3">
              <div className="text-xs text-gray-400">Abstain rate</div>
              <div className="text-2xl font-bold text-gray-100">
                {report.abstain_rate !== undefined ? `${(report.abstain_rate * 100).toFixed(1)}%` : '—'}
              </div>
            </div>
            <div className="bg-gray-800 border border-gray-700 rounded-lg p-3">
              <div className="text-xs text-gray-400">Classes</div>
              <div className="text-2xl font-bold text-gray-100">{report.class_labels?.length ?? 9}</div>
            </div>
          </div>

          {/* Confusion matrix */}
          {report.confusion_matrix && report.class_labels && (
            <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
              <h2 className="text-sm font-semibold text-gray-400 mb-3">Confusion Matrix</h2>
              <p className="text-xs text-gray-500 mb-3">Rows = true class, Columns = predicted class. Green = correct, Red = error.</p>
              <ConfusionMatrix matrix={report.confusion_matrix} labels={report.class_labels} />
            </div>
          )}

          {/* Per-class metrics */}
          {report.per_class && (
            <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
              <h2 className="text-sm font-semibold text-gray-400 mb-3">Per-class Metrics</h2>
              <PerClassTable perClass={report.per_class} />
            </div>
          )}

          {/* Detectors */}
          {report.detectors && (
            <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
              <h2 className="text-sm font-semibold text-gray-400 mb-3">Detectors</h2>
              <DetectorTable detectors={report.detectors} />
            </div>
          )}

          {/* Unseen faults */}
          <div className="bg-gray-800 border border-gray-700 rounded-lg p-4">
            <h2 className="text-sm font-semibold text-gray-400 mb-3">Unseen Fault Results</h2>
            <p className="text-xs text-gray-500 mb-3">
              Safety criterion: unseen faults must never trigger AUTO actions.
            </p>
            {report.unseen_fault_results ? (
              <UnseenFaultPanel data={report.unseen_fault_results} />
            ) : (
              <p className="text-xs text-gray-600">
                No unseen fault evaluation data — the safety rate is not evaluated.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
