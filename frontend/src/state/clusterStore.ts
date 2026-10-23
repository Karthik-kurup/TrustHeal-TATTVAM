/**
 * state/clusterStore.ts — Live cluster telemetry and narration.
 *
 * Updated from WS 'tick', 'fault_event', and 'narration' messages.
 */

import { create } from 'zustand';
import type { TickPayload, FaultEventPayload, NarrationPayload } from '../types/ws';

export interface NarrationEntry {
  id: number;
  text: string;
  level: 'info' | 'warn' | 'error' | 'success';
  sim_time: number;
  incident_id?: string;
  service?: string;
}

export interface ActiveFault {
  fault_id: string;
  fault_type: string;
  target: string;
}

interface ClusterStore {
  simTime: number;
  services: TickPayload['services'];
  nodes: TickPayload['nodes'];
  openIncidentServices: string[];
  activeFaults: Record<string, ActiveFault>;

  narration: NarrationEntry[];
  _narrationCounter: number;

  setTick: (simTime: number, payload: TickPayload) => void;
  setActiveFaults: (p: FaultEventPayload) => void;
  addNarration: (
    entry: Omit<NarrationPayload, 'text'> & { text: string; sim_time: number }
  ) => void;
  clearNarration: () => void;
}

const MAX_NARRATION = 200;

export const useClusterStore = create<ClusterStore>((set, get) => ({
  simTime: 0,
  services: {},
  nodes: {},
  openIncidentServices: [],
  activeFaults: {},

  narration: [],
  _narrationCounter: 0,

  setTick: (simTime, payload) =>
    set({
      simTime,
      services: payload.services,
      nodes: payload.nodes,
      openIncidentServices: payload.open_incidents,
    }),

  setActiveFaults: (p) =>
    set((state) => {
      const next = { ...state.activeFaults };
      if (p.active) {
        next[p.fault_id] = {
          fault_id: p.fault_id,
          fault_type: p.fault_type,
          target: p.target,
        };
      } else {
        delete next[p.fault_id];
      }
      return { activeFaults: next };
    }),

  addNarration: (entry) =>
    set((state) => {
      const id = state._narrationCounter + 1;
      const newEntry: NarrationEntry = {
        id,
        text: entry.text,
        level: entry.level ?? 'info',
        sim_time: entry.sim_time,
        incident_id: entry.incident_id,
        service: entry.service,
      };
      const narration = [newEntry, ...state.narration].slice(0, MAX_NARRATION);
      return { narration, _narrationCounter: id };
    }),

  clearNarration: () => set({ narration: [], _narrationCounter: 0 }),
}));
