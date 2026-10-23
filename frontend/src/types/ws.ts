/**
 * types/ws.ts — TypeScript types mirroring backend app/ws.py WsMessage.
 * These are hand-maintained to match the Python dataclasses exactly.
 */

export type WsMessageType =
  | 'tick'
  | 'cluster_event'
  | 'fault_event'
  | 'incident_update'
  | 'diagnosis'
  | 'trust'
  | 'decision'
  | 'action'
  | 'verification'
  | 'narration'
  | 'audit'
  | 'sim_state';

export interface WsMessage<P = Record<string, unknown>> {
  type: WsMessageType;
  sim_time: number;
  incident_id: string | null;
  payload: P;
}

// ── Payload shapes (used in stores and components) ────────────────────────────

export interface ServiceMetrics {
  cpu: number;
  memory: number;
  request_rate: number;
  error_rate: number;
  latency_p50: number;
  latency_p95: number;
  restarts: number;
  replicas: number;
  network_errors: number;
}

export interface NodeMetrics {
  cpu: number;
  memory: number;
  status: string; // Ready / NotReady / Unknown
}

export interface TickPayload {
  services: Record<string, ServiceMetrics>;
  nodes: Record<string, NodeMetrics>;
  open_incidents: string[]; // service ids
  active_faults: string[];  // fault ids
}

export interface FaultEventPayload {
  fault_id: string;
  fault_type: string;
  target: string;
  active: boolean;
  sim_time: number;
}

export interface TrustSignals {
  U: number;
  S: number;
  N: number;
  H: number;
  E: number;
  D: number;
  P_correct: number;
}

export interface DiagnosisPayload {
  incident_id: string;
  service: string;
  label: string;
  confidence: number;
  probs: Record<string, number>;
  mlp_label: string;
  xgb_label: string;
  evidence_label: string;
  top_features: Record<string, number>;
  mlp_uncertainty: number;
  abstained: boolean;
}

export interface TrustPayload {
  incident_id: string;
  service: string;
  signals: TrustSignals;
  vetoes: string[];
}

export interface DecisionPayload {
  incident_id: string;
  service: string;
  gate_decision: 'AUTO' | 'ASK' | 'ESCALATE';
  action_name: string;
  action_target: string;
  action_risk: string;
  p_correct: number;
  vetoes: string[];
}

export interface ActionPayload {
  incident_id: string;
  service: string;
  action: string;
  target: string;
  risk_class: string;
  attempt: number;
  result_ok: boolean;
  result_detail: string;
  dry_run_ok: boolean;
  undo_executed: boolean;
  executed_at?: number;
}

export interface VerificationPayload {
  incident_id: string;
  service: string;
  health_status: 'OK' | 'BAD' | 'WORSE';
  resolved: boolean;
  metric_snapshot: Record<string, number>;
}

export interface NarrationPayload {
  text: string;
  level: 'info' | 'warn' | 'error' | 'success';
  incident_id?: string;
  service?: string;
}

export interface IncidentUpdatePayload {
  incident_id: string;
  service: string;
  status: string;
  opened_at: number;
  closed_at?: number;
  gate_decision?: string;
  action_name?: string;
  time_to_recover?: number;
}

export interface SimStatePayload {
  status: 'running' | 'paused' | 'stopped';
  speed: number;
  sim_time: number;
  seed: number;
  autonomy_stage: string;
  kill_switch: boolean;
}

export interface AuditPayload {
  action: string;
  service: string;
  actor: string;
  outcome: string;
  sim_time: number;
  detail: string;
}
