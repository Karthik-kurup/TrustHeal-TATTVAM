/**
 * state/incidentStore.ts — Incident state derived from WS events.
 */

import { create } from 'zustand';
import type {
  DiagnosisPayload,
  TrustPayload,
  DecisionPayload,
  ActionPayload,
  VerificationPayload,
  IncidentUpdatePayload,
} from '../types/ws';

export interface IncidentSummary {
  incident_id: string;
  service: string;
  status: string;
  opened_at: number;
  closed_at?: number;
  gate_decision?: string;
  action_name?: string;
  time_to_recover?: number;
  diagnosis?: DiagnosisPayload;
  trust?: TrustPayload;
  decision?: DecisionPayload;
  actions: ActionPayload[];
  verification?: VerificationPayload;
}

export interface IncidentApiRecord {
  id: string;
  service: string;
  status: string;
  opened_at: number;
  closed_at?: number | null;
  time_to_recover?: number | null;
  diagnosis?: Omit<DiagnosisPayload, 'incident_id' | 'service'> | null;
  trust?: TrustPayload['signals'] | null;
  vetoes?: string[];
  gate_decision?: string | null;
  action_name?: string;
  action_target?: string;
  action_risk?: string;
  actions?: Array<Omit<ActionPayload, 'incident_id' | 'service'>>;
  verification?: {
    health_status: VerificationPayload['health_status'];
    resolved: boolean;
    metric_snapshot: Record<string, number>;
  } | null;
}

interface IncidentStore {
  incidents: Record<string, IncidentSummary>; // incident_id -> summary

  hydrateIncidents: (records: IncidentApiRecord[]) => void;
  upsertIncident: (p: IncidentUpdatePayload) => void;
  setDiagnosis: (id: string, d: DiagnosisPayload) => void;
  setTrust: (id: string, t: TrustPayload) => void;
  setDecision: (id: string, d: DecisionPayload) => void;
  addAction: (id: string, a: ActionPayload) => void;
  setVerification: (id: string, v: VerificationPayload) => void;

  openIncidents: () => IncidentSummary[];
  recentIncidents: (n?: number) => IncidentSummary[];
}

export const useIncidentStore = create<IncidentStore>((set, get) => ({
  incidents: {},

  hydrateIncidents: (records) =>
    set((state) => {
      const incidents = { ...state.incidents };
      for (const record of records) {
        const incidentId = record.id;
        const existing = incidents[incidentId];
        const decision = record.gate_decision
          ? {
              incident_id: incidentId,
              service: record.service,
              gate_decision: record.gate_decision as 'AUTO' | 'ASK' | 'ESCALATE',
              action_name: record.action_name ?? '',
              action_target: record.action_target ?? '',
              action_risk: record.action_risk ?? '',
              p_correct: record.trust?.P_correct ?? 0,
              vetoes: record.vetoes ?? [],
            }
          : undefined;
        incidents[incidentId] = {
          ...existing,
          incident_id: incidentId,
          service: record.service,
          status: record.status,
          opened_at: record.opened_at,
          closed_at: record.closed_at ?? undefined,
          gate_decision: record.gate_decision ?? undefined,
          action_name: record.action_name || undefined,
          time_to_recover: record.time_to_recover ?? undefined,
          diagnosis: record.diagnosis
            ? {
                ...record.diagnosis,
                incident_id: incidentId,
                service: record.service,
              }
            : existing?.diagnosis,
          trust: record.trust
            ? {
                incident_id: incidentId,
                service: record.service,
                signals: record.trust,
                vetoes: record.vetoes ?? [],
              }
            : existing?.trust,
          decision: decision ?? existing?.decision,
          actions: (record.actions ?? []).map((action) => ({
            ...action,
            incident_id: incidentId,
            service: record.service,
          })),
          verification: record.verification
            ? {
                ...record.verification,
                incident_id: incidentId,
                service: record.service,
              }
            : existing?.verification,
        };
      }
      return { incidents };
    }),

  upsertIncident: (p) =>
    set((state) => ({
      incidents: {
        ...state.incidents,
        [p.incident_id]: {
          ...(state.incidents[p.incident_id] ?? { actions: [] }),
          incident_id: p.incident_id,
          service: p.service,
          status: p.status,
          opened_at: p.opened_at,
          closed_at: p.closed_at,
          gate_decision: p.gate_decision ?? state.incidents[p.incident_id]?.gate_decision,
          action_name: p.action_name ?? state.incidents[p.incident_id]?.action_name,
          time_to_recover: p.time_to_recover ?? state.incidents[p.incident_id]?.time_to_recover,
        },
      },
    })),

  setDiagnosis: (id, d) =>
    set((state) => ({
      incidents: {
        ...state.incidents,
        [id]: { ...(state.incidents[id] ?? { actions: [] }), diagnosis: d },
      },
    })),

  setTrust: (id, t) =>
    set((state) => ({
      incidents: {
        ...state.incidents,
        [id]: { ...(state.incidents[id] ?? { actions: [] }), trust: t },
      },
    })),

  setDecision: (id, d) =>
    set((state) => ({
      incidents: {
        ...state.incidents,
        [id]: {
          ...(state.incidents[id] ?? { actions: [] }),
          decision: d,
          gate_decision: d.gate_decision,
          action_name: d.action_name,
        },
      },
    })),

  addAction: (id, a) =>
    set((state) => {
      const prev = state.incidents[id] ?? { actions: [] };
      return {
        incidents: {
          ...state.incidents,
          [id]: { ...prev, actions: [...(prev.actions ?? []), a] },
        },
      };
    }),

  setVerification: (id, v) =>
    set((state) => ({
      incidents: {
        ...state.incidents,
        [id]: { ...(state.incidents[id] ?? { actions: [] }), verification: v },
      },
    })),

  openIncidents: () =>
    Object.values(get().incidents).filter(
      (i) => !['RESOLVED', 'ESCALATED'].includes(i.status)
    ),

  recentIncidents: (n = 20) =>
    Object.values(get().incidents)
      .sort((a, b) => b.opened_at - a.opened_at)
      .slice(0, n),
}));
