/**
 * hooks/useWs.ts — WebSocket connection hook.
 *
 * Connects to /ws, parses typed WsMessage events, and dispatches
 * to the appropriate Zustand store. Reconnects automatically on disconnect.
 */

import { useEffect, useRef } from 'react';
import { useSimStore } from '../state/simStore';
import { useIncidentStore, type IncidentApiRecord } from '../state/incidentStore';
import { useClusterStore } from '../state/clusterStore';
import { requestJson } from '../api/request';
import type {
  WsMessage,
  TickPayload,
  FaultEventPayload,
  DiagnosisPayload,
  TrustPayload,
  DecisionPayload,
  ActionPayload,
  VerificationPayload,
  NarrationPayload,
  SimStatePayload,
} from '../types/ws';

const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;
const RECONNECT_DELAY_MS = 2000;

export function useWs(): void {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(false);

  const setSimState = useSimStore((s) => s.setSimState);
  const setTick = useClusterStore((s) => s.setTick);
  const addNarration = useClusterStore((s) => s.addNarration);
  const setActiveFaults = useClusterStore((s) => s.setActiveFaults);
  const hydrateIncidents = useIncidentStore((s) => s.hydrateIncidents);
  const setDiagnosis = useIncidentStore((s) => s.setDiagnosis);
  const setTrust = useIncidentStore((s) => s.setTrust);
  const setDecision = useIncidentStore((s) => s.setDecision);
  const addAction = useIncidentStore((s) => s.addAction);
  const setVerification = useIncidentStore((s) => s.setVerification);

  const connect = () => {
    if (!mountedRef.current) return;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      useSimStore.getState().setConnected(true);
      void Promise.all([
        requestJson<Omit<SimStatePayload, 'autonomy_stage' | 'kill_switch'>>('/api/sim/state'),
        requestJson<{ autonomy_stage: string; kill_switch: boolean }>('/api/control/state'),
      ])
        .then(([sim, control]) => {
          setSimState({
            ...sim,
            autonomy_stage: control.autonomy_stage,
            kill_switch: control.kill_switch,
          });
        })
        .catch((error: unknown) => {
          console.error('Unable to load initial simulation state:', error);
        });
    };

    ws.onclose = () => {
      useSimStore.getState().setConnected(false);
      wsRef.current = null;
      if (mountedRef.current) {
        reconnectTimer.current = setTimeout(connect, RECONNECT_DELAY_MS);
      }
    };

    ws.onerror = () => {
      ws.close();
    };

    ws.onmessage = (event: MessageEvent) => {
      let msg: WsMessage;
      try {
        msg = JSON.parse(event.data as string) as WsMessage;
      } catch (error) {
        console.error('Ignoring invalid WebSocket message:', error);
        return;
      }

      switch (msg.type) {
        case 'sim_state': {
          const p = msg.payload as unknown as SimStatePayload;
          setSimState(p);
          break;
        }
        case 'tick': {
          const p = msg.payload as unknown as TickPayload;
          setTick(msg.sim_time, p);
          // Keep sim_time in sim store updated from tick messages too
          useSimStore.getState().setSimTime(msg.sim_time);
          break;
        }
        case 'fault_event': {
          const p = msg.payload as unknown as FaultEventPayload;
          setActiveFaults(p);
          if (p.active) {
            addNarration({
              text: `Fault injected: ${p.fault_type} on ${p.target}`,
              level: 'warn',
              sim_time: msg.sim_time,
            });
          } else {
            addNarration({
              text: `Fault cleared: ${p.fault_type} on ${p.target}`,
              level: 'info',
              sim_time: msg.sim_time,
            });
          }
          break;
        }
        case 'incident_update': {
          const p = msg.payload as unknown as IncidentApiRecord;
          hydrateIncidents([p]);
          break;
        }
        case 'diagnosis': {
          const p = msg.payload as unknown as { diagnosis: Omit<DiagnosisPayload, 'incident_id' | 'service'> };
          const incidentId = msg.incident_id ?? '';
          const service = useIncidentStore.getState().incidents[incidentId]?.service ?? '';
          setDiagnosis(incidentId, { ...p.diagnosis, incident_id: incidentId, service });
          break;
        }
        case 'trust': {
          const p = msg.payload as unknown as {
            trust: TrustPayload['signals'];
            vetoes: string[];
            decision: DecisionPayload['gate_decision'];
            action: string;
            target: string;
            risk_class: string;
          };
          const incidentId = msg.incident_id ?? '';
          const service = useIncidentStore.getState().incidents[incidentId]?.service ?? '';
          setTrust(incidentId, { incident_id: incidentId, service, signals: p.trust, vetoes: p.vetoes });
          setDecision(incidentId, {
            incident_id: incidentId,
            service,
            gate_decision: p.decision,
            action_name: p.action,
            action_target: p.target,
            action_risk: p.risk_class,
            p_correct: p.trust.P_correct,
            vetoes: p.vetoes,
          });
          break;
        }
        case 'decision': {
          const p = msg.payload as unknown as DecisionPayload;
          setDecision(p.incident_id, p);
          if (p.gate_decision === 'ASK') {
            addNarration({
              text: `ASK approval needed for ${p.action_name} on ${p.service}`,
              level: 'warn',
              sim_time: msg.sim_time,
              incident_id: p.incident_id,
            });
          }
          break;
        }
        case 'action': {
          const p = msg.payload as unknown as Omit<ActionPayload, 'incident_id' | 'service'>;
          const incidentId = msg.incident_id ?? '';
          const service = useIncidentStore.getState().incidents[incidentId]?.service ?? '';
          addAction(incidentId, { ...p, incident_id: incidentId, service });
          addNarration({
            text: `Action: ${p.action} on ${p.target} — ${p.result_ok ? 'OK' : 'FAILED'}`,
            level: p.result_ok ? 'success' : 'error',
            sim_time: msg.sim_time,
            incident_id: incidentId,
          });
          break;
        }
        case 'verification': {
          const p = msg.payload as unknown as {
            result?: Omit<VerificationPayload, 'incident_id' | 'service'> | null;
          };
          const incidentId = msg.incident_id ?? '';
          const service = useIncidentStore.getState().incidents[incidentId]?.service ?? '';
          if (!p.result) break;
          setVerification(incidentId, { ...p.result, incident_id: incidentId, service });
          addNarration({
            text: `Verification: ${service} is ${p.result.health_status}${p.result.resolved ? ' — RESOLVED' : ''}`,
            level: p.result.resolved ? 'success' : p.result.health_status === 'WORSE' ? 'error' : 'warn',
            sim_time: msg.sim_time,
            incident_id: incidentId,
          });
          break;
        }
        case 'narration': {
          const p = msg.payload as unknown as NarrationPayload;
          addNarration({ ...p, sim_time: msg.sim_time });
          break;
        }
        case 'cluster_event': {
          addNarration({
            text: (msg.payload as { detail?: string }).detail ?? JSON.stringify(msg.payload),
            level: 'info',
            sim_time: msg.sim_time,
          });
          break;
        }
        case 'audit': {
          // Audit entries rendered on Control Center — just update narration for severity
          break;
        }
        default:
          break;
      }
    };
  };

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // prevent reconnect on intentional close
        wsRef.current.close();
        wsRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
