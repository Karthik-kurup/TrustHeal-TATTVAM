/**
 * components/GuidedDemo.tsx — Guided Demo overlay.
 *
 * A floating overlay that chains scripted demo steps with narration captions.
 * Each step injects a fault / changes a setting and shows a caption.
 * Press "Next" to advance. "Run Demo" button appears in the TopBar.
 */

import React, { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { requestJson } from '../api/request';

// ── Demo step definitions ─────────────────────────────────────────────────────

interface DemoStep {
  id: number;
  title: string;
  caption: string;
  action?: () => Promise<void>;
  navigateTo?: string;
  waitSeconds?: number; // suggested wait before clicking Next
}

async function postControl(cmd: string, value?: number) {
  await requestJson('/api/sim/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd, value }),
  });
}

async function injectFault(kind: string, target: string, intensity = 1.0, duration = 300) {
  await requestJson('/api/faults/inject', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, target, intensity, duration }),
  });
}

async function setAutonomyStage(stage: string) {
  await requestJson('/api/control/mode', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ autonomy_stage: stage }),
  });
}

async function setKillSwitch(enabled: boolean) {
  await requestJson('/api/control/killswitch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
}

// ── Page-local step definitions ───────────────────────────────────────────────
// Steps are defined inside the component so they can use the navigate function

// ── GuidedDemo component ──────────────────────────────────────────────────────

export default function GuidedDemo({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [running, setRunning] = useState(false);
  const [log, setLog] = useState<string[]>([]);

  const STEPS: DemoStep[] = [
    {
      id: 0,
      title: 'Step 1 — Steady State',
      caption:
        'The Shop cluster is running healthy. Notice the green service grid, flat metrics, and live narration. ' +
        'The simulation is at 10x speed. Autonomy stage is S3 (Auto Low-risk).',
      navigateTo: '/',
      action: async () => {
        await postControl('play');
        await postControl('speed', 10);
        await setAutonomyStage('S3');
        await setKillSwitch(false);
      },
      waitSeconds: 15,
    },
    {
      id: 1,
      title: 'Step 2 — Memory Exhaustion on checkout (AUTO)',
      caption:
        'Injecting memory exhaustion on the checkout service. ' +
        'Watch the Incident War Room: the pipeline opens an incident, ' +
        'diagnoses MEMORY_EXHAUSTION, and the Trust Gate approves an automatic restart.',
      navigateTo: '/incidents',
      action: async () => {
        await injectFault('memory_exhaustion', 'checkout', 1.0, 300);
      },
      waitSeconds: 30,
    },
    {
      id: 2,
      title: 'Step 3 — CPU Overload on frontend (ASK)',
      caption:
        'Now injecting CPU overload on frontend. The Trust Gate will request human approval (ASK) ' +
        'because the diagnosis needs confirmation. Click Approve in the War Room to proceed.',
      navigateTo: '/incidents',
      action: async () => {
        await injectFault('cpu_stress', 'frontend', 1.2, 300);
      },
      waitSeconds: 30,
    },
    {
      id: 3,
      title: 'Step 4 — Bad Deployment on payment',
      caption:
        'A bad deployment on payment causes error rates to spike immediately after the rollout. ' +
        'Both the evidence rules and the ML models agree: BAD_DEPLOYMENT. Rollback proposed.',
      navigateTo: '/incidents',
      action: async () => {
        await injectFault('bad_deploy', 'payment', 1.0, 400);
      },
      waitSeconds: 30,
    },
    {
      id: 4,
      title: 'Step 5 — Unseen Fault: DNS Failure (ESCALATE)',
      caption:
        'Injecting a DNS failure — this is an unseen fault not in training data. ' +
        'Notice the N (novelty) signal is high and the OOD veto fires. ' +
        'The Trust Gate ESCALATEs instead of acting. Triage explains why.',
      navigateTo: '/incidents',
      action: async () => {
        await injectFault('dns_failure', 'catalog', 1.0, 300);
      },
      waitSeconds: 30,
    },
    {
      id: 5,
      title: 'Step 6 — Node Failure (reschedule + approval)',
      caption:
        'One of the worker nodes goes down. Pods go Unknown then get rescheduled. ' +
        'Reschedule is R3 risk — always requires approval. Check the topology view.',
      navigateTo: '/twin',
      action: async () => {
        await injectFault('node_failure', 'node-2', 1.0, 400);
      },
      waitSeconds: 30,
    },
    {
      id: 6,
      title: 'Step 7 — Kill Switch',
      caption:
        'Activating the kill switch mid-incident. All autonomous actions are immediately suspended. ' +
        'The system drops to recommend-only. Deactivate to restore normal operation.',
      navigateTo: '/control',
      action: async () => {
        await setKillSwitch(true);
      },
      waitSeconds: 10,
    },
    {
      id: 7,
      title: 'Step 8 — Compare Lab',
      caption:
        'Finally, the Compare Lab runs the same fault schedule in three arms: ' +
        'K8s defaults (no healing), rule-based, and full TrustHeal. ' +
        'Click "Run Comparison" to see the MTTR and unsafe action comparison.',
      navigateTo: '/compare',
      action: async () => {
        await setKillSwitch(false);
      },
      waitSeconds: 5,
    },
  ];

  const currentStep = STEPS[step] ?? STEPS[STEPS.length - 1];
  const isLast = step >= STEPS.length - 1;

  const executeStep = useCallback(async () => {
    setRunning(true);
    const s = STEPS[step];
    const msgs: string[] = [];
    try {
      if (s.navigateTo) navigate(s.navigateTo);
      if (s.action) {
        await s.action();
        msgs.push(`Step ${s.id + 1}: ${s.title} — executed`);
      }
    } catch (e) {
      msgs.push(`Step ${s.id + 1}: error — ${String(e)}`);
    }
    setLog((prev) => [...prev, ...msgs]);
    setRunning(false);
  }, [step, navigate]);

  const handleNext = async () => {
    await executeStep();
    if (!isLast) setStep((s) => s + 1);
  };

  const handlePrev = () => {
    if (step > 0) setStep((s) => s - 1);
  };

  return (
    <div className="fixed bottom-6 right-6 z-50 w-96 bg-gray-900 border border-gray-600 rounded-xl shadow-2xl overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-gray-800 border-b border-gray-700">
        <span className="text-xs font-semibold text-teal-300 uppercase tracking-wider">
          Guided Demo
        </span>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">{step + 1}/{STEPS.length}</span>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-200 text-lg leading-none"
          >
            ×
          </button>
        </div>
      </div>

      {/* Step content */}
      <div className="p-4">
        <h3 className="text-sm font-semibold text-gray-200 mb-2">{currentStep.title}</h3>
        <p className="text-xs text-gray-400 leading-relaxed mb-3">{currentStep.caption}</p>

        {currentStep.waitSeconds && (
          <div className="text-xs text-gray-600 mb-3">
            Suggested wait: ~{currentStep.waitSeconds}s before clicking Next
          </div>
        )}

        {/* Progress dots */}
        <div className="flex gap-1.5 mb-4">
          {STEPS.map((_, i) => (
            <div
              key={i}
              className={`h-1.5 rounded-full transition-all ${
                i < step ? 'bg-teal-600 flex-1' :
                i === step ? 'bg-teal-400 flex-1' :
                'bg-gray-700 w-4'
              }`}
            />
          ))}
        </div>

        {/* Buttons */}
        <div className="flex gap-2">
          {step > 0 && (
            <button
              onClick={handlePrev}
              className="px-3 py-1.5 rounded bg-gray-700 text-gray-300 text-xs hover:bg-gray-600"
            >
              Back
            </button>
          )}
          <button
            onClick={handleNext}
            disabled={running}
            className={`flex-1 py-2 rounded text-sm font-semibold transition-colors disabled:opacity-50 ${
              isLast
                ? 'bg-teal-700 hover:bg-teal-600 text-white'
                : 'bg-blue-800 hover:bg-blue-700 text-blue-100'
            }`}
          >
            {running ? 'Running…' : isLast ? 'Finish Demo' : `Execute & Next`}
          </button>
        </div>
      </div>

      {/* Log tail */}
      {log.length > 0 && (
        <div className="px-4 pb-3 text-xs text-gray-500 font-mono">
          {log.slice(-2).map((l, i) => <div key={i}>{l}</div>)}
        </div>
      )}
    </div>
  );
}
