/**
 * state/simStore.ts — Simulation control state.
 *
 * Mirrors the backend SimLoop + ControlState. Populated by WS sim_state
 * and tick messages, plus REST calls from UI controls.
 */

import { create } from 'zustand';
import type { SimStatePayload } from '../types/ws';

export type SimStatus = 'running' | 'paused' | 'stopped';
export type AutonomyStage = 'S0' | 'S1' | 'S2' | 'S3' | 'S4';

interface SimStore {
  // Connection
  connected: boolean;
  setConnected: (v: boolean) => void;

  // Simulation time & control
  simTime: number;
  status: SimStatus;
  speed: number;
  seed: number;

  // Control plane
  autonomyStage: AutonomyStage;
  killSwitch: boolean;

  // Setters
  setSimTime: (t: number) => void;
  setSimState: (p: SimStatePayload) => void;
  setControlState: (stage: AutonomyStage, killSwitch: boolean) => void;
  setSpeed: (s: number) => void;
  setAutonomyStage: (stage: AutonomyStage) => void;
  setKillSwitch: (v: boolean) => void;
}

export const useSimStore = create<SimStore>((set) => ({
  connected: false,
  setConnected: (v) => set({ connected: v }),

  simTime: 0,
  status: 'paused',
  speed: 10,
  seed: 42,

  autonomyStage: 'S3',
  killSwitch: false,

  setSimTime: (simTime) => set({ simTime }),

  setSimState: (p) =>
    set({
      status: p.status,
      speed: p.speed,
      simTime: p.sim_time,
      seed: p.seed,
      autonomyStage: p.autonomy_stage as AutonomyStage,
      killSwitch: p.kill_switch,
    }),

  setControlState: (autonomyStage, killSwitch) => set({ autonomyStage, killSwitch }),
  setSpeed: (speed) => set({ speed }),
  setAutonomyStage: (autonomyStage) => set({ autonomyStage }),
  setKillSwitch: (killSwitch) => set({ killSwitch }),
}));
