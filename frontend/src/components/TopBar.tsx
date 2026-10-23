/**
 * components/TopBar.tsx — Persistent application header.
 *
 * Shows: TrustHeal logo, sim clock, status indicator, speed controls,
 * autonomy stage badge, WS connection status, and the prominent kill switch.
 */

import React, { useState } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { Moon, Sun } from 'lucide-react';
import { useSimStore } from '../state/simStore';
import type { AutonomyStage } from '../state/simStore';
import { requestJson } from '../api/request';

const SPEED_OPTIONS = [1, 5, 10, 30] as const;
const NAV_ITEMS = [
  ['/', 'Mission Control'],
  ['/metrics', 'Metrics'],
  ['/incidents', 'War Room'],
  ['/twin', 'Twin Studio'],
  ['/control', 'Control'],
  ['/models', 'Models'],
  ['/compare', 'Compare Lab'],
] as const;

const STAGE_LABELS: Record<AutonomyStage, string> = {
  S0: 'S0 Shadow',
  S1: 'S1 Recommend',
  S2: 'S2 Approve',
  S3: 'S3 Auto Low',
  S4: 'S4 Auto Med',
};

const STAGE_COLORS: Record<AutonomyStage, string> = {
  S0: 'bg-gray-700 text-gray-300',
  S1: 'bg-blue-900 text-blue-200',
  S2: 'bg-purple-900 text-purple-200',
  S3: 'bg-teal-900 text-teal-200',
  S4: 'bg-green-900 text-green-200',
};

function formatSimTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m.toString().padStart(2, '0')}m ${s.toString().padStart(2, '0')}s`;
  return `${m.toString().padStart(2, '0')}m ${s.toString().padStart(2, '0')}s`;
}

async function postControl(cmd: string, value?: number) {
  await requestJson('/api/sim/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd, value }),
  });
}

interface TopBarProps {
  onRunDemo?: () => void;
  theme: 'dark' | 'light';
  onToggleTheme: () => void;
}

export default function TopBar({ onRunDemo, theme, onToggleTheme }: TopBarProps) {
  const { connected, simTime, status, speed, autonomyStage, killSwitch, setKillSwitch } = useSimStore();
  const [controlError, setControlError] = useState('');
  const location = useLocation();
  const navigate = useNavigate();

  const runControl = async (action: () => Promise<void>) => {
    setControlError('');
    try {
      await action();
    } catch (error) {
      setControlError(error instanceof Error ? error.message : 'Control request failed');
    }
  };

  const handlePlayPause = () => runControl(() =>
    postControl(status === 'running' ? 'pause' : 'play')
  );

  const handleSpeed = (s: number) => runControl(() => postControl('speed', s));

  const handleKillSwitch = () => runControl(async () => {
    const enabled = !killSwitch;
    await requestJson('/api/control/killswitch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    setKillSwitch(enabled);
  });

  const toggleTheme = () => {
    onToggleTheme();
    setControlError('');
  };

  const changeSpeed = (s: number) => {
    handleSpeed(s);
  };

  const handleThemeLabel = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';

  const handlePlayPauseClick = () => {
    handlePlayPause();
  };

  return (
    <header className="relative flex items-center justify-between min-h-12 px-4 py-1 border-b border-gray-700 bg-gray-900 shrink-0 z-50 gap-3">
      {/* Left: logo + nav */}
      <div className="flex items-center gap-6">
        <span className="font-bold tracking-wide text-blue-400 text-base select-none">
          &#9096; TrustHeal
        </span>
        <nav className="hidden xl:flex items-center gap-1 text-xs overflow-x-auto whitespace-nowrap">
          {NAV_ITEMS.map(([to, label]) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `px-2 py-1 rounded text-gray-400 hover:text-gray-100 transition-colors ${
                  isActive ? 'text-gray-100 bg-gray-800' : ''
                }`
              }
            >
              {label}
            </NavLink>
          ))}
        </nav>
        <select
          className="xl:hidden w-28 rounded border border-gray-600 bg-gray-800 px-2 py-1 text-xs text-gray-200"
          aria-label="Navigate to page"
          value={location.pathname.startsWith('/incidents') ? '/incidents' : location.pathname}
          onChange={(event) => navigate(event.target.value)}
        >
          {NAV_ITEMS.map(([to, label]) => <option key={to} value={to}>{label}</option>)}
        </select>
      </div>

      {/* Centre: clock + controls */}
      <div className="flex items-center gap-3 text-sm">
        {/* Connection dot */}
        <span
          className={`w-2 h-2 rounded-full ${connected ? 'bg-green-400' : 'bg-red-500 animate-pulse'}`}
          title={connected ? 'WS connected' : 'WS disconnected'}
        />

        {/* Sim clock */}
        <span className="font-mono text-gray-200 min-w-[80px] text-center">
          {formatSimTime(simTime)}
        </span>

        {/* Play / Pause */}
        <button
          onClick={handlePlayPauseClick}
          className="px-2 py-0.5 rounded text-xs border border-gray-600 hover:border-gray-400 text-gray-300 hover:text-gray-100 transition-colors"
          title={status === 'running' ? 'Pause' : 'Play'}
        >
          {status === 'running' ? '⏸' : '▶'}
        </button>

        {/* Speed selector */}
        <div className="flex items-center gap-0.5">
          {SPEED_OPTIONS.map((s) => (
            <button
              key={s}
              onClick={() => changeSpeed(s)}
              className={`px-1.5 py-0.5 rounded text-xs transition-colors ${
                Math.round(speed) === s
                  ? 'bg-blue-700 text-white'
                  : 'text-gray-400 hover:text-gray-200'
              }`}
            >
              {s}x
            </button>
          ))}
        </div>
      </div>

      {/* Right: autonomy stage + kill switch */}
      <div className="flex items-center gap-2">
        <button
          onClick={toggleTheme}
          className="inline-flex items-center gap-1.5 rounded border border-gray-600 px-2 py-1 text-xs text-gray-300 hover:text-gray-100 transition-colors"
          aria-label={handleThemeLabel}
          title={handleThemeLabel}
        >
          {theme === 'dark' ? <Sun size={14} aria-hidden="true" /> : <Moon size={14} aria-hidden="true" />}
          <span className="hidden xl:inline">{theme === 'dark' ? 'Light' : 'Dark'}</span>
        </button>
        {/* Autonomy stage badge */}
        <span
          className={`px-2 py-0.5 rounded text-xs font-medium ${
            STAGE_COLORS[autonomyStage] ?? 'bg-gray-700 text-gray-300'
          }`}
          title="Autonomy stage — change in Control Center"
        >
          {STAGE_LABELS[autonomyStage] ?? autonomyStage}
        </span>

        {/* Kill switch */}
        <button
          onClick={handleKillSwitch}
          className={`px-3 py-1 rounded font-semibold text-xs transition-all border ${
            killSwitch
              ? 'bg-red-600 border-red-400 text-white animate-pulse'
              : 'bg-gray-800 border-red-700 text-red-400 hover:bg-red-900 hover:text-red-200'
          }`}
          title={killSwitch ? 'Kill switch ON — click to restore' : 'Kill switch — drops to recommend-only'}
        >
          {killSwitch ? '⚠ KILL SW ON' : '⚠ Kill Switch'}
        </button>

        {/* Run Demo button */}
        {onRunDemo && (
          <button
            onClick={onRunDemo}
            className="px-3 py-1 rounded font-semibold text-xs bg-teal-900 border border-teal-600 text-teal-300 hover:bg-teal-800 transition-colors"
            title="Open the guided demo overlay"
          >
            &#9654; Run Demo
          </button>
        )}
      </div>
      {controlError && (
        <div className="absolute right-4 top-full mt-1 max-w-sm rounded border border-red-700 bg-red-950 px-3 py-2 text-xs text-red-200 shadow-lg" role="alert">
          {controlError}
        </div>
      )}
    </header>
  );
}
