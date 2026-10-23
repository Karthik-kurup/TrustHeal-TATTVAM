# Changelog

All notable changes to TrustHeal are recorded here.

## [M0] — Repo skeleton, health endpoint, frontend scaffold

### Added
- `plans/00-master-plan.md` — approved master plan with all amendments applied
- `AGENTS.md` — working agreement, architecture, commands, rules
- `CHANGELOG.md` — this file
- `docs/KNOWN_GAPS.md` — placeholder (no known gaps yet)
- `Makefile` — `dev`, `install`, `train`, `test`, `demo`, `typecheck` targets
- `docker-compose.yml` — service stubs for backend and frontend
- `backend/pyproject.toml` — pinned Python dependencies
- `backend/app/main.py` — FastAPI factory with CORS and `GET /api/health`
- `backend/app/config.py` — Pydantic BaseSettings
- `backend/app/api/health.py` — health route
- Backend directory skeleton: all subpackage `__init__.py` stubs
- `frontend/` — Vite + React 18 + TypeScript + Tailwind scaffold
- `frontend/src/pages/HealthCheck.tsx` — placeholder page calling `/api/health`
- `frontend/vite.config.ts` — proxy config (`/api` → `:8000`, `/ws` → `ws::8000`)
- `scripts/smoke_check.py` — basic smoke check

### Changed
- Nothing (initial commit)

### Fixed
- Nothing (initial commit)

## [M4] — Detectors, dataset generation, model training

### Added
- `backend/diagnosis/ensemble.py` — weighted MLP(0.45)+XGB(0.45)+EV(0.10) ensemble
  with UNKNOWN abstention rule (max_conf < tau OR models disagree AND evidence silent)
- `backend/training/generate_dataset.py` — headless episode runner; 240 episodes × 715 sim-s;
  sweeps intensity, onset shape, targets; outputs `models/dataset.npz` (28800 labelled samples)
- `backend/training/train.py` — full training pipeline: detectors, classifiers, tau_abstain
  (ensemble-based, no LOO re-training), OOD Mahalanobis threshold, saves `models/thresholds.json`
- `backend/training/evaluate.py` — stratified held-out evaluation; confusion matrix, F1 per class,
  detector AUROC comparison, calibration data, unseen-fault escalation rate; writes `models/report.json`
- `backend/tests/test_evidence_rules.py` — 20 new tests for evidence rules and ensemble
- `models/dataset.npz` — 28800 labelled training samples (8 classes, 64 features)
- `models/mlp_mcd.pt` — trained MLP (60 epochs, 28800 samples)
- `models/xgb.json` — trained XGBoost (200 trees, 97% train accuracy)
- `models/iforest.pkl` — trained IsolationForest detector
- `models/autoencoder.pt` — trained autoencoder detector
- `models/thresholds.json` — tau_abstain=0.30, OOD threshold=28.8
- `models/report.json` — evaluation report (F1=0.70 weighted avg, 12.6% abstain rate)

### Changed
- `backend/diagnosis/xgb.py` — fixed label-encoder mismatch (XGBoost returns probs in
  alphabetical order, not CLASS_ORDER); removed deprecated `use_label_encoder` / `num_class` params
- `backend/training/evaluate.py` — stratified per-class held-out split (prevents class-imbalanced test set)
- `backend/diagnosis/ensemble.py` — default tau lowered from 0.55 to 0.30 to reflect diluted MLP confidence

### Fixed
- XGBoost `predict_proba` was returning probabilities mapped to wrong class labels (alphabetical
  vs CLASS_ORDER ordering) — caused 0% XGB accuracy; fixed with label encoder inverse mapping

### Test counts
- Previous: 57 tests
- Added: 20 tests (evidence rules + ensemble)
- Total: **77 tests passing**

### Known limitations
- MLP F1 is dragged down by untrained-batch eval; XGB alone achieves ~95% in-sample; full ensemble
  weighted average F1=0.70 on held-out (stratified). NODE_FAILURE F1=0.44, NORMAL F1=0.38 —
  both can be improved with more training epochs in make train (see docs/KNOWN_GAPS.md).
- No unseen-fault rows in dataset yet (unseen faults not injected during generation by design);
  unseen-fault evaluation will be populated in M10 when unseen episodes are added.
- `make train` runtime: ~250s on CPU-only (dataset gen ~45s, classifiers ~200s, eval ~5s).
  Under 90s constraint requires GPU or parallel generation — logged in KNOWN_GAPS.md.

## [M5] - Trust signals, gate, runbook, executor, verifier

### Added
- `backend/trust/signals.py` - Six trust signals: U (MC-dropout entropy), S (attribution
  stability under perturbation), N (Mahalanobis OOD distance), H (health agreement),
  E (evidence-ML agreement 1/0.5/0), D (MLP-XGB diagnoser agreement)
- `backend/trust/calibration.py` - Logistic calibration of P_correct from signals; loads
  weights from models/calibration.json; default prior weights
- `backend/trust/gate.py` - Trust Gate: hard vetoes (VETO_UNKNOWN, VETO_OOD, VETO_E_ZERO,
  VETO_KILL_SWITCH) + autonomy-stage default mode + P_correct threshold -> AUTO/ASK/ESCALATE
- `backend/trust/action_policy.py` - ACTION_RISK table, per-stage action mode matrix,
  kill switch override; stages S0-S4 per spec
- `backend/healing/runbook.py` - Diagnosis-to-action ladder (max 5 attempts/incident);
  all 9 fault classes mapped per spec
- `backend/healing/guardrails.py` - GuardrailManager: allow-list, per-service budget,
  per-cluster budget, cooldown tracking (sim-time based)
- `backend/healing/executor.py` - Guarded executor: dry-run -> guardrails -> audit-before ->
  execute via ClusterAdapter -> audit-after; undo() for scale_up
- `backend/healing/verifier.py` - Post-action verifier: PENDING/RESOLVED/STILL_BAD/WORSE
  based on health status and metric comparison
- `backend/store/incidents.py` - IncidentStore + full Incident Pydantic model (diagnosis,
  trust, actions, verification, event_timeline, ground_truth); IncidentStatus enum
- `backend/store/audit.py` - AuditLog: append-only, 2000-entry ring, filter by service/type
- `backend/tests/test_pipeline.py` - 34 new tests for gate vetoes, runbook, guardrails,
  incident store, audit log, verifier
- `backend/app/sim_loop.py` - Rewritten to wire full pipeline per tick: health ->
  incident open/close -> ensemble diagnosis (stable k=3) -> trust gate -> executor ->
  verifier; ControlState dataclass replaces old dict

### Changed
- `backend/app/api/incidents.py` - Wired to real IncidentStore; approve/reject update Incident
- `backend/app/api/control.py` - Wired to sim.control ControlState; limits propagate to GuardrailManager
- `backend/app/api/audit.py` - Wired to real AuditLog; filters by service/event_type

### Test counts
- Previous: 77 tests
- Added: 34 tests (pipeline: gate, runbook, guardrails, store, audit, verifier)
- Total: **111 tests passing**

### Known behaviour
- OOD veto (VETO_OOD) fires frequently in live sim because training distribution Mahalanobis
  threshold (28.8) is tight relative to live feature distributions. This is safe conservative
  behaviour (escalates rather than wrongly acting) but can be relaxed via the Control Center
  threshold slider in M9. Logged as GAP-004 in KNOWN_GAPS.md.

## [M6] — Frontend foundation: routing, state, WebSocket hook, Mission Control

### Added
- `frontend/src/types/ws.ts` — TypeScript types for all WS message types and payloads
  (tick, fault_event, incident_update, diagnosis, trust, decision, action, verification,
  narration, audit, sim_state); hand-maintained to match backend `app/ws.py` exactly
- `frontend/src/hooks/useWs.ts` — WebSocket connection hook; connects to `/ws`,
  auto-reconnects on disconnect, parses typed WsMessage and dispatches to Zustand stores
- `frontend/src/state/simStore.ts` — Zustand store for simulation time, status, speed,
  seed, autonomy stage, kill switch, WS connection status
- `frontend/src/state/incidentStore.ts` — Zustand store for incidents keyed by incident_id;
  merges diagnosis, trust, decision, actions, verification from WS events
- `frontend/src/state/clusterStore.ts` — Zustand store for live service metrics, node
  metrics, active faults, narration feed (ring-capped at 200 entries)
- `frontend/src/components/TopBar.tsx` — Persistent header: TrustHeal logo, page nav,
  WS connection dot, sim clock, play/pause button, 1x/5x/10x/30x speed buttons,
  autonomy stage badge, prominent kill switch button
- `frontend/src/pages/MissionControl.tsx` — Home dashboard: health score tile, node/pod/
  incident/MTTR/automation-rate KPI tiles, service health grid with colour coding,
  active faults list, open incidents list, live narration feed (scrollable, colour-coded)
- `frontend/src/pages/MetricsExplorer.tsx` — Stub page (M7)
- `frontend/src/pages/IncidentWarRoom.tsx` — Functional stub: lists recent incidents with
  status/diagnosis/gate_decision badges
- `frontend/src/pages/TwinStudio.tsx` — Functional stub: simulation controls + fault injector
  UI wired to `/api/faults/inject` and `/api/sim/control`
- `frontend/src/pages/ControlCenter.tsx` — Functional stub: autonomy stage selector
  (PUT /api/control/mode) + kill switch toggle (POST /api/control/killswitch)
- `frontend/src/pages/ModelsAndTrust.tsx` — Functional stub: fetches and renders
  `GET /api/models/report` evaluation table
- `frontend/src/pages/CompareLab.tsx` — Stub page (M11)
- `frontend/src/App.tsx` — Full BrowserRouter routing; `Layout` component mounts `useWs`
  once and renders `TopBar` + `Routes` for all 7 pages

### Changed
- `frontend/src/App.tsx` — Replaced M0 single-page placeholder with full routing layout
- `frontend/package.json` — Added `react-router-dom`, `lucide-react`, `framer-motion`,
  `recharts`, `@xyflow/react` (installed via npm install)

### Notes
- TypeScript typecheck: clean (0 errors)
- Production build: 209 kB JS + 15.5 kB CSS, built in ~1.5s
- Backend: 111 tests still passing (no backend changes in M6)
- All WS event dispatching tested manually against live backend


## [M11] — Compare Lab + Guided Demo

### Added
- `backend/app/api/compare.py` — Full Compare Lab API: 3-arm headless lockstep runner
  (A0=k8s_defaults, A1=rule_based, A2=full_trustheal), POST /compare/run, GET /compare/{id},
  GET /compare/{id}/metrics. Arms run in parallel with asyncio.gather().
- `frontend/src/pages/CompareLab.tsx` — Compare Lab UI: preset fault schedules, seed/duration
  config, start button, polling, MTTR table with best-arm highlighting, 4 overlaid metric charts
- `frontend/src/components/GuidedDemo.tsx` — 8-step floating demo overlay with captions,
  progress dots, fault injection / control actions wired to REST API
- `frontend/src/components/TopBar.tsx` — "Run Demo" button (teal, top-right)

### Changed
- `frontend/src/App.tsx` — Wired GuidedDemo into Layout; passes onRunDemo prop to TopBar

### Notes
- Compare Lab runs 3 headless arms in parallel; 900 sim-s run completes in ~5s wall time
- Guided Demo scripts the 8-step hackathon script (spec §13)

## [M10] — Triage, Models & Trust page, LLM interface

### Added
- `backend/triage/templates.py` — Deterministic template triage: description, ranked hypotheses
  with evidence refs, runbook steps, kubectl command preview; all claims evidence-referenced
- `backend/triage/llm_triage.py` — TriageProvider ABC + TemplateProvider (default, offline) +
  LLMProvider (openai-compatible, falls back to template on any error); factory get_triage_provider()
- `backend/app/api/models_report.py` — GET /api/models/report (serves report.json),
  GET /api/models/status (artefact presence check)
- `frontend/src/pages/ModelsAndTrust.tsx` — Full models page: model artefact status,
  confusion matrix (colour-scaled cells), per-class F1/precision/recall table,
  detector AUROC comparison, unseen fault results panel

### Changed
- `backend/app/sim_loop.py` — _schedule_triage() + _run_triage() added; triage fires
  as background asyncio task on ASK and ESCALATE decisions (never on AUTO)
- `backend/app/main.py` — models_router and compare_router registered

## [M9] — Digital Twin Studio + Control Center

### Added / Changed
- `frontend/src/pages/TwinStudio.tsx` — Full TwinStudio: live topology + fault injector
  (kind, target, intensity, duration sliders) + SnapshotPanel (save/restore) + ScenarioPanel
- `frontend/src/pages/ControlCenter.tsx` — Full ControlCenter: kill switch, autonomy stage
  selector, action risk table, precision threshold sliders, rate limit sliders, audit log table

## [M8] — Incident War Room

### Added
- `frontend/src/pages/IncidentWarRoom.tsx` — Complete War Room: incident list sidebar,
  7-stage pipeline stepper, diagnosis panel (class probs, diagnoser agreement, top features),
  Trust Gate panel (P_correct gauge, U/S/N/H/E/D bars, veto badges), decision card
  (AUTO/ASK/ESCALATE badge, action details), Approve/Reject buttons for WAITING incidents,
  action log, verification panel

## [M7] — Topology view + Metrics Explorer

### Added
- `frontend/src/components/Topology.tsx` — React Flow topology: service nodes with pod cells,
  call-graph edges coloured by error rate (width = rps), fault badges, incident badges,
  live data from Zustand clusterStore, MiniMap
- `frontend/src/pages/MetricsExplorer.tsx` — Per-service metric explorer: 6 charts per service
  (CPU, memory, latency p95, error rate, request rate, restarts), live ring buffer (120 ticks),
  fault/incident/action vertical markers, baseline ±15% band, service filter

## [M6] — Frontend foundation

### Added
(full details in original entry below)

