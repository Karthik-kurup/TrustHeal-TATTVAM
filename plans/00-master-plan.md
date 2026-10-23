# TrustHeal — Master Plan
> Status: **APPROVED** — amendments applied 2025-01

---

## 1. Goal and Scope

Build **TrustHeal**: a trust-aware, explainable, self-healing system for Kubernetes that runs entirely on a laptop with no cloud account and no real cluster. It includes:

- A **digital twin** that simulates a realistic Kubernetes cluster (`shop` namespace).
- A **healing pipeline**: detect → diagnose → trust gate → execute → verify.
- A **rich web frontend** with live event streaming.
- A **guided 6-minute demo** that showcases every pipeline stage.

Everything is MUST-priority unless explicitly labelled SHOULD or COULD.

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│ Backend (Python 3.11 · FastAPI · Uvicorn)                        │
│                                                                  │
│  ┌─────────┐  ┌────────────┐  ┌────────────┐  ┌─────────────┐  │
│  │  Twin   │  │ Telemetry  │  │  Pipeline  │  │   Healing   │  │
│  │ (sim)   │→ │  +Features │→ │ Detect/   │→ │ Executor+   │  │
│  │         │  │  +Health   │  │ Diagnose/ │  │ Verifier    │  │
│  └────┬────┘  └────────────┘  │ Trust Gate│  └─────────────┘  │
│       │                       └────────────┘                    │
│       │  ClusterAdapter interface                               │
│       │                                                          │
│  ┌────┴────────────────────────────────────────────────────┐    │
│  │  Store: incidents (SQLite file), audit (SQLite file),   │    │
│  │         telemetry ring buffer (in-memory), models/      │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                  │
│  FastAPI: /api/* REST  +  /ws WebSocket                         │
└─────────────────────────────┬────────────────────────────────────┘
                              │ HTTP + WS
┌─────────────────────────────▼────────────────────────────────────┐
│ Frontend (React 18 · TypeScript · Vite · Tailwind · shadcn/ui)  │
│ Pages: Mission Control · Metrics · War Room · Twin Studio ·      │
│        Control Center · Models & Trust · Compare Lab             │
└──────────────────────────────────────────────────────────────────┘
```

### Key design decisions (approved)

| Decision | Choice |
|---|---|
| Simulation loop | Hybrid: asyncio background loop drives the demo; `/sim/control` with `step` command for tests. Pause freezes the clock. |
| Physics tick rate | 1 simulated second per physics step; telemetry sampled to the pipeline every 15 sim-s; UI state batched at 5–10 Hz wall time. |
| Training data generation | Headless (no sleep), pure in-process computation — episodes are milliseconds of CPU time. `make train` generates extra episodes until minimum sample sizes are met; fails loudly with a warning if still insufficient after max attempts. |
| Calibration fallback | If Wilson lower-bound precision cannot be proved for R1/R2, gate defaults to ASK (action proposed, never AUTO). ESCALATE only if no calibrated gate exists at all. |
| Persistence | File-backed SQLite for audit + incidents; in-memory ring buffer for live telemetry. Model artefacts in `models/`. |
| Primary one-command start | `make dev` — Uvicorn + `vite dev` as concurrent processes. |
| TypeScript type generation | `@hey-api/openapi-ts` from the FastAPI-generated OpenAPI schema. |
| M0 frontend scaffold | Vite + TypeScript + Tailwind + one placeholder page calling `/api/health`, proxy and CORS working, OpenAPI type generation wired. shadcn components deferred to M6. |
| Compare Lab arms | A0 = Kubernetes defaults (probe restarts + HPA on CPU); A1 = rule-based (same health trigger, runbook, executor, guardrails, verification as TrustHeal — threshold + evidence-rule diagnosis only, no ML, no gate); A2 = full TrustHeal. Arms differ only in ML + gate. |
| RNG streams | Separate seeded streams per arm for workload, fault schedule and dynamics noise. Actions in one arm cannot perturb another arm's random sequence. |
| Topology | 6 services, 14 pods at start, replica ceiling 6 per service. Node capacity sized so any single node failure still allows all pods to reschedule. |

---

## 3. File Tree

```
trustheal/
├── AGENTS.md
├── CHANGELOG.md
├── Makefile
├── docker-compose.yml
├── README.md
├── plans/
│   └── 00-master-plan.md
├── docs/
│   └── KNOWN_GAPS.md
├── scripts/
│   ├── start_dev.sh
│   └── smoke_check.py
├── models/                        # trained artefacts (gitignored binaries)
│   └── report.json
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py                # FastAPI app factory
│   │   ├── config.py              # Settings (Pydantic BaseSettings)
│   │   ├── ws.py                  # WebSocket hub
│   │   └── api/
│   │       ├── sim.py             # /sim/*
│   │       ├── cluster.py         # /cluster
│   │       ├── metrics.py         # /metrics/query
│   │       ├── incidents.py       # /incidents/*
│   │       ├── actions.py         # /actions/manual
│   │       ├── control.py         # /control/*
│   │       ├── models.py          # /models/report
│   │       ├── compare.py         # /compare/*
│   │       ├── audit.py           # /audit
│   │       ├── faults.py          # /faults/*
│   │       └── scenarios.py       # /scenarios/*
│   ├── twin/
│   │   ├── cluster.py             # ClusterState, Node, Pod, Service
│   │   ├── workload.py            # LoadGenerator, traffic patterns
│   │   ├── dynamics.py            # per-tick physics (CPU, memory, latency, errors)
│   │   ├── faults.py              # FaultCatalogue, FaultInstance
│   │   ├── scheduler.py           # simulated kube-scheduler (reschedule, evict)
│   │   ├── hpa.py                 # simulated HPA
│   │   ├── snapshots.py           # snapshot / restore
│   │   └── scenarios/
│   │       ├── shop_default.json  # baseline scenario (no faults)
│   │       └── demo.json          # guided demo fault schedule
│   ├── adapters/
│   │   ├── base.py                # ClusterAdapter ABC
│   │   ├── sim_adapter.py         # wraps twin
│   │   └── kube_adapter.py        # COULD — read-only real adapter (disabled)
│   ├── telemetry/
│   │   ├── store.py               # ring buffer + SQLite for incidents
│   │   ├── features.py            # 64-feature window vector
│   │   ├── evidence.py            # hard evidence features
│   │   └── health.py              # health check with hysteresis
│   ├── detection/
│   │   ├── rules.py               # threshold rule detector
│   │   ├── iforest.py             # Isolation Forest
│   │   └── autoencoder.py         # small autoencoder (PyTorch)
│   ├── diagnosis/
│   │   ├── mlp_mcd.py             # MC-dropout MLP (PyTorch)
│   │   ├── xgb.py                 # XGBoost classifier
│   │   ├── evidence_rules.py      # deterministic evidence rules
│   │   └── ensemble.py            # combine A+B+C → ensemble prediction
│   ├── trust/
│   │   ├── signals.py             # U, S, N, H, E, D signals
│   │   ├── gate.py                # fusion logistic + isotonic → P_correct → AUTO/ASK/ESCALATE
│   │   ├── action_policy.py       # risk class + runbook ladder
│   │   └── calibration.py         # leave-one-episode-out fit + isotonic
│   ├── healing/
│   │   ├── runbook.py             # per-diagnosis action ladder
│   │   ├── executor.py            # guarded executor with audit
│   │   ├── guardrails.py          # allow-list, budgets, cooldowns, disruption budget
│   │   └── verifier.py            # wait, evaluate, undo, outcome label
│   ├── triage/
│   │   ├── llm_triage.py          # provider interface
│   │   └── templates.py           # deterministic template (default)
│   ├── store/
│   │   ├── incidents.py           # SQLite incident store (SQLModel)
│   │   └── audit.py               # append-only audit log (SQLite)
│   └── training/
│       ├── generate_dataset.py    # headless twin episodes → labelled feature rows
│       ├── train.py               # fit MLP, XGBoost, IForest, AE, gate
│       └── evaluate.py            # confusion matrix, unseen faults, report.json
│   └── tests/
│       ├── test_twin.py
│       ├── test_features.py
│       ├── test_health.py
│       ├── test_evidence_rules.py
│       ├── test_gate.py
│       ├── test_executor.py
│       └── test_api.py
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── tailwind.config.ts
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── api/
        │   ├── client.ts          # generated by @hey-api/openapi-ts
        │   └── ws.ts              # typed WebSocket hook
        ├── state/
        │   ├── simStore.ts        # Zustand: sim clock, speed, autonomy stage
        │   ├── incidentStore.ts   # Zustand: open incidents
        │   └── clusterStore.ts    # Zustand: topology, node/pod states
        ├── components/
        │   ├── layout/
        │   │   ├── TopBar.tsx
        │   │   └── Sidebar.tsx
        │   ├── topology/
        │   │   ├── ClusterFlow.tsx # React Flow canvas
        │   │   ├── NodeCard.tsx
        │   │   ├── PodCell.tsx
        │   │   └── ServiceEdge.tsx
        │   ├── charts/
        │   │   ├── MetricChart.tsx
        │   │   └── TrustRadar.tsx
        │   ├── incidents/
        │   │   ├── PipelineStepper.tsx
        │   │   ├── TrustGatePanel.tsx
        │   │   ├── EvidencePanel.tsx
        │   │   ├── DiagnosisPanel.tsx
        │   │   └── TriagePanel.tsx
        │   └── demo/
        │       └── GuidedDemoOverlay.tsx
        └── pages/
            ├── MissionControl.tsx
            ├── MetricsExplorer.tsx
            ├── IncidentWarRoom.tsx
            ├── DigitalTwinStudio.tsx
            ├── ControlCenter.tsx
            ├── ModelsAndTrust.tsx
            └── CompareLab.tsx
```

---

## 4. Data Models

### 4.1 Core Pydantic models (backend)

```python
# twin/cluster.py
class PodPhase(str, Enum): Running, Pending, Unknown, Succeeded, Failed, CrashLoopBackOff, OOMKilled

class Pod(BaseModel):
    id: str; service: str; node: str
    phase: PodPhase; cpu_util: float; mem_bytes: int
    restarts: int; termination_reason: str | None; exit_code: int | None
    revision: str; ready: bool

class Node(BaseModel):
    id: str; ready: bool; cpu_capacity: float; mem_capacity: int
    cpu_used: float; mem_used: int

class Service(BaseModel):
    id: str; replicas: int; ready_replicas: int
    request_rate: float; error_rate: float; latency_p50: float; latency_p95: float
    revision: str; hpa_desired: int | None

class ClusterSnapshot(BaseModel):
    sim_time: float; nodes: list[Node]; pods: list[Pod]; services: list[Service]
    events: list[ClusterEvent]
```

```python
# telemetry/features.py
class FeatureVector(BaseModel):
    sim_time: float; service: str
    features: list[float]           # length 64
    feature_names: list[str]        # length 64
    window_start: float; window_end: float
```

```python
# store/incidents.py
class IncidentStatus(str, Enum): OPEN, RESOLVED, ESCALATED

class DiagnosisResult(BaseModel):
    label: str; confidence: float; class_probs: dict[str, float]
    mlp_probs: dict[str, float]; xgb_probs: dict[str, float]
    evidence_label: str; top_features: list[tuple[str, float]]
    shap_values: list[float] | None

class TrustSignals(BaseModel):
    U: float; S: float; N: float; H: float; E: float; D: float
    p_correct: float; veto_reasons: list[str]

class GateDecision(str, Enum): AUTO, ASK, ESCALATE

class ActionRecord(BaseModel):
    action: str; risk_class: str; target: str
    dry_run_ok: bool; guardrail_checks: list[str]
    executed_at: float | None; undone: bool; outcome: str | None

class DiagnosisHistoryEntry(BaseModel):
    sim_time: float; result: DiagnosisResult; stable: bool

class TimelineEvent(BaseModel):
    sim_time: float; event_type: str; detail: dict   # for incident replay

class Incident(BaseModel):
    id: str; service: str; opened_at: float; closed_at: float | None
    status: IncidentStatus
    feature_vector: FeatureVector                    # window at incident open
    metric_window: list[dict]                        # raw metric samples around incident (for verification curve)
    diagnosis: DiagnosisResult | None                # latest stable diagnosis
    diagnosis_history: list[DiagnosisHistoryEntry]   # all intermediate diagnoses
    trust_signals: TrustSignals | None
    gate_decision: GateDecision | None
    actions: list[ActionRecord]
    verified_ok: bool | None
    triage_summary: str | None
    ground_truth: str | None        # set only by inject metadata
    narration: list[str]
    event_timeline: list[TimelineEvent]              # persisted for replay
```

```python
# store/audit.py
class AuditEntry(BaseModel):
    id: str; sim_time: float; wall_time: float
    incident_id: str | None; action: str; actor: str
    detail: dict; outcome: str
```

### 4.2 WebSocket message envelope

```python
class WsMessageType(str, Enum):
    tick = "tick"; cluster_event = "cluster_event"; fault_event = "fault_event"
    incident_update = "incident_update"; diagnosis = "diagnosis"
    trust = "trust"; decision = "decision"; action = "action"
    verification = "verification"; narration = "narration"
    audit = "audit"; sim_state = "sim_state"

class WsMessage(BaseModel):
    type: WsMessageType; sim_time: float; incident_id: str | None
    payload: dict         # validated downstream per type
```

---

## 5. API Contract

All REST under `/api`. Detailed parameter lists are in inline docstrings on each route; this section captures the inventory.

### 5.1 Simulation
- `GET /api/sim/state` → `SimState` (running, speed, sim_time, seed, active_faults)
- `POST /api/sim/control` body `{cmd: play|pause|step|speed|reset|seed, value?}`
- `POST /api/sim/snapshot` → `{snapshot_id}`
- `POST /api/sim/restore` body `{snapshot_id}`

### 5.2 Scenarios & Faults
- `GET /api/scenarios`
- `POST /api/scenarios/{id}/run`
- `POST /api/faults/inject` body `FaultSpec`
- `GET /api/faults/active`
- `DELETE /api/faults/{id}`

### 5.3 Cluster
- `GET /api/cluster` → `ClusterSnapshot` (topology + current state)

### 5.4 Metrics
- `GET /api/metrics/query?service=&metric=&from=&to=` → `list[MetricPoint]`

### 5.5 Incidents
- `GET /api/incidents`
- `GET /api/incidents/{id}`
- `POST /api/incidents/{id}/approve`
- `POST /api/incidents/{id}/reject`

### 5.6 Actions
- `POST /api/actions/manual` body `{action, target, dry_run?}`

### 5.7 Control
- `GET/PUT /api/control/mode`
- `GET/PUT /api/control/thresholds`
- `GET/PUT /api/control/limits`
- `POST /api/control/killswitch` body `{enabled: bool}`

### 5.8 Models
- `GET /api/models/report` → `ModelReport` (confusion matrix, calibration, unseen results)

### 5.9 Compare Lab (SHOULD)
- `POST /api/compare/run` body `{scenario_id, arms: list}`
- `GET /api/compare/{id}`

### 5.10 Audit
- `GET /api/audit?from=&to=&incident_id=`

### 5.11 WebSocket
- `WS /ws` — typed `WsMessage` stream

---

## 6. Trust Gate Logic

```
Feature vector (64d)
        │
        ├─► MLP-MCD (30 passes) ──────────────────► mlp_probs, U (entropy of mean)
        ├─► XGBoost ──────────────────────────────► xgb_probs
        └─► EvidenceRules ────────────────────────► evidence_label

Ensemble (weighted vote) → ensemble_label, ensemble_probs

UNKNOWN abstention rule (derived from training data only — no tuning on unseen faults):
  UNKNOWN if:
    max(ensemble_probs) < τ_abstain                  (low peak confidence)
    OR (argmax(mlp_probs) != argmax(xgb_probs)       (model disagreement)
        AND evidence_label == "UNKNOWN")              (evidence rules silent)
  τ_abstain is the 10th percentile of max(ensemble_probs) on correct training predictions.

Trust signals:
  U = MC-dropout epistemic uncertainty — entropy of mean class distribution over 30 passes
  S = rank correlation of top-k SHAP attributions under small input perturbations
  N = Mahalanobis distance to training distribution, combined with detector scores
  H = at decision time: 1 if health still BAD and diagnosis is a fault class;
      0 if health has recovered or diagnosis is NORMAL
  E = evidence agreement: 1 confirm / 0.5 silent / 0 contradict
  D = agreement between MLP and XGBoost: 1 agree / 0 disagree

Fusion logistic(U,S,N,H,E,D) + isotonic calibration → P_correct

OOD threshold:  99th percentile of leave-episode-out Mahalanobis distances on training data.
Never tuned on unseen faults.

Hard vetoes (not learned):
  - N > OOD_THRESHOLD               → ESCALATE
  - E == 0 AND risk >= R2           → ESCALATE
  - ensemble_label == UNKNOWN       → ESCALATE

Gate thresholds (per risk class, set by calibration):
  - P_correct >= τ_auto(risk)       → AUTO
  - τ_ask <= P_correct < τ_auto     → ASK
  - P_correct < τ_ask               → ESCALATE

τ values chosen so Wilson lower-bound precision ≥ 95% (R1) / 99% (R2).
If insufficient calibration data: R1/R2 actions default to ASK; logged in KNOWN_GAPS.
If no calibrated gate exists at all: all actions default to ESCALATE.
```

---

## 7. Simulation Loop Design

```
SimLoop (asyncio.Task)
│
├── running = True | paused | stopped
├── speed_factor: float  (1x, 5x, 10x, 30x)
├── physics_step_sim  = 1s             (simulated seconds per physics step)
├── telemetry_interval_sim = 15s       (pipeline samples every 15 sim-s = 15 physics steps)
├── ui_batch_interval_wall = 0.1s      (flush WS batch to frontend at ~10 Hz)
├── sleep_wall_per_physics = 1.0 / speed_factor  (wall seconds per physics step)
│
└── per physics step (every 1 sim-s):
    1. Advance faults: apply scheduled injections, age active faults
    2. dynamics.step(cluster_state, active_faults, dt=1)  → updated cluster + events
    3. Accumulate cluster events into WS batch
    4. IF sim_time % 15 == 0  (telemetry tick):
           a. telemetry.record(snapshot)
           b. FOR each service:
                  features = feature_engine.compute(ring_buffer, service)
                  IF features is not None (window full):
                      health.evaluate(features, service)  → open/close incidents
           c. FOR each open incident:
                  diagnosis = ensemble.predict(features)
                  incident.diagnosis_history.append(diagnosis)
                  IF stable(diagnosis, k=3) OR timeout:
                      trust = signals.compute(features, diagnosis)
                      decision = gate.decide(trust, diagnosis, autonomy_stage)
                      incident.update(diagnosis, trust, decision)
                      IF decision == AUTO:
                          executor.run(action, incident)
                      ELSE:
                          emit WS decision (ASK/ESCALATE)
           d. FOR each open incident awaiting verification:
                  verifier.check(incident)
    5. IF wall_elapsed >= ui_batch_interval_wall:
           Flush WS batch (tick, cluster_events, narration, incident_updates)
    6. sleep(sleep_wall_per_physics) if running else wait for resume event
```

**Diagnosis stability rule:** a diagnosis is considered stable when the ensemble_label has not changed for k consecutive telemetry ticks (default k=3) OR when a maximum-wait timeout (5 telemetry ticks) is reached. The UI receives every intermediate diagnosis in `diagnosis_history` for the evolving-diagnosis display.

**Diagnosis history:** every `DiagnosisResult` for an incident is appended to `incident.diagnosis_history: list[DiagnosisResult]`. The final decision uses the most recent stable result. The War Room shows the full history as a timeline.

Step-on-demand (tests): call `SimLoop.step_physics()` to advance 1 sim-s; call `SimLoop.step_telemetry()` to advance 15 sim-s. Both bypass sleep.

---

## 8. Feature Engineering

64-feature vector, computed over a 5-minute (20-telemetry-tick) sliding window.
The window contains 20 samples spaced 15 sim-s apart (physics runs at 1 sim-s but telemetry is sampled every 15 sim-s).

**Per-metric features (×9 metrics × 5 stats = 45):**

Metrics (9 — no duplicates): cpu_util, mem_ratio, latency_p50, latency_p95, error_rate, request_rate, network_errors, ready_ratio, restart_count

Stats per metric: mean, max, std, slope (linear fit), last — all normalised relative to the service's own baseline (first 4 minutes of clean operation).

Note: `restart_delta` was a duplicate of `restart_count`; removed. `restart_delta_window` was a duplicate of the restart_count slope/mean; removed. The 9-metric × 5-stat = 45 per-metric block plus 19 context features gives exactly 64.

**Event and context features (19):**
1. last_termination_reason_oom (bool → float)
2. last_exit_code_normalised (exit code / 255)
3. minutes_since_last_revision
4. error_rate_change_post_rollout
5. node_ready_fraction (ready nodes / total nodes)
6. unknown_pods_count_normalised (unknown pods / total pods)
7. request_rate_ratio_vs_baseline
8. hpa_desired_vs_current (0 if HPA disabled)
9. event_OOMKilled_count_window
10. event_BackOff_count_window
11. event_NodeNotReady_count_window
12. detector_rules_score
13. detector_iforest_score
14. detector_autoencoder_score
15. restart_delta_last_tick (restarts this tick vs previous tick)
16. latency_p95_ratio_vs_baseline (redundant with slot 4 but kept as a strong signal)
17. error_spike_post_revision (bool → float: error_rate > 2× baseline within 10 min of revision)
18. request_rate_above_hpa_ceiling (bool → float)
19. symptom_overlap_score (cosine similarity to nearest known-fault centroid in training space — helps diagnosis when symptoms overlap between fault types)

> **Symptom overlap design note:** cpu_stress vs traffic_surge overlap on CPU and request_rate; memory_leak vs memory_exhaustion overlap on mem_ratio; slow_dependency (unseen) vs network_partition overlap on latency. Feature 19 encodes proximity to the nearest centroid but does NOT identify which fault — the classifier does that. This makes correct classification non-trivial (honest < 100% F1 expected) and makes the OOD signal meaningful for unseen faults.

---

## 9. Runbook and Retry Ladder

See spec section 8.6. The executor enforces:
- Cap 5 attempts per incident
- Cooldown between rungs (60 s simulated)
- Automatic undo on verification failure
- Escalate on max attempts

---

## 10. Milestone Breakdown

### M0 — /init, master plan, repo skeleton, Makefile
**Intent:** Establish foundations — plan approved, skeleton checked in, health endpoint live, minimal frontend scaffold wired.

**Acceptance:** `make dev` starts both Uvicorn and Vite; `GET /api/health` returns 200; frontend placeholder page calls `/api/health` and shows the response; OpenAPI type generation step runs without errors; plan file committed.

**Tasks:**
- [x] Write plans/00-master-plan.md
- [x] Apply approved amendments to plans/00-master-plan.md
- [ ] Create AGENTS.md with architecture summary, commands, rules
- [ ] Create backend directory skeleton (all `__init__.py` stubs, `pyproject.toml` with pinned deps)
- [ ] Implement `GET /api/health` and `GET /api/health` → `{status: ok, sim_time: 0}` (FastAPI app factory, config, minimal main.py)
- [ ] Makefile targets: `dev`, `train`, `test`, `demo`, `typecheck`, `install`
- [ ] docker-compose.yml stubs
- [ ] docs/KNOWN_GAPS.md placeholder
- [ ] CHANGELOG.md
- [ ] Frontend scaffold: Vite + React 18 + TypeScript + Tailwind init
- [ ] Frontend placeholder page (`src/pages/HealthCheck.tsx`) that calls `/api/health` and renders the response
- [ ] Vite proxy config: `/api` → `http://localhost:8000`, `/ws` → `ws://localhost:8000`
- [ ] CORS config in FastAPI to allow Vite dev origin
- [ ] `make typecheck`: export `backend/openapi.json` → run `@hey-api/openapi-ts` → write `frontend/src/api/client.ts`

**Status:** [x] done

---

### M1 — Twin core
**Intent:** A simulated Kubernetes cluster that reacts to fault injections deterministically.

**Acceptance:** Same seed + scenario → same telemetry; each of the 7 known faults visibly changes at least 2 metrics; `pytest tests/test_twin.py` green; RNG stream isolation test passes (identical workload and fault sequences across arms despite different healing actions).

**Topology (Shop namespace start state):**

| Service | Start pods | Call graph |
|---|---|---|
| frontend | 3 | → catalog, cart, checkout |
| catalog | 3 | → cache |
| cart | 2 | — |
| checkout | 3 | → payment, cart |
| payment | 2 | — |
| cache | 1 | — |
| **Total** | **14** | |

Node capacity: 4 worker nodes, each with enough CPU and memory headroom so that after losing any one node, all 14 pods reschedule successfully on the remaining 3.

**Tasks:**
- [ ] `twin/cluster.py` — Pydantic models: Pod, Node, Service, ClusterSnapshot, ClusterEvent
- [ ] `twin/workload.py` — LoadGenerator with daily pattern + noise + benign bursts; three separate seeded RNG streams (workload, fault_schedule, dynamics_noise); seed-controlled and reproducible per arm
- [ ] `twin/dynamics.py` — physics step at dt=1 sim-s: CPU model, memory model (leak slope, OOMKilled), latency queueing curve, error model, dependency propagation (cascade), network loss model
- [ ] `twin/faults.py` — FaultCatalogue (12 faults with overlapping symptoms per design note in §8), FaultInstance, apply/age
- [ ] `twin/scheduler.py` — simulated reschedule, node eviction, pod Unknown → Pending → Running with delays; validate that single node loss is survivable
- [ ] `twin/hpa.py` — HPA observer, scale event
- [ ] `twin/snapshots.py` — copy-on-write cluster state snapshot/restore
- [ ] `adapters/base.py` — ClusterAdapter ABC
- [ ] `adapters/sim_adapter.py` — wraps twin, implements ABC; exposes separate RNG streams per arm for Compare Lab
- [ ] `backend/tests/test_twin.py` — determinism test, 7 fault effects, OOM, cascade, node failure survivability, RNG stream isolation test
- [ ] Scenario JSON: `shop_default.json`, `demo.json`

**Status:** [x] done

---

### M2 — Telemetry, features, health check
**Intent:** Convert raw snapshots into incident-triggering feature windows.

**Acceptance:** Injecting memory_exhaustion opens an incident within 2 minutes simulated; removing the fault closes it after hysteresis; no flapping on stable data; `pytest tests/test_features.py tests/test_health.py` green.

**Tasks:**
- [ ] `telemetry/store.py` — in-memory ring buffer (deque per service), SQLite for incidents/events
- [ ] `telemetry/features.py` — 64-feature window vector computation, baseline capture (first 4 min)
- [ ] `telemetry/evidence.py` — hard evidence feature extraction
- [ ] `telemetry/health.py` — hysteresis state machine (BAD after 2 bad / 1 severe, OK after 4 good, one incident per service)
- [ ] Unit tests: feature shapes, baseline normalisation, hysteresis transitions

**Status:** [x] done

---

### M3 — FastAPI REST and WebSocket stream
**Intent:** Expose the twin and pipeline through a typed API; stream live events to the UI.

**Acceptance:** All REST endpoints return correct schema; `/ws` delivers `tick` messages at the configured speed; OpenAPI JSON generated; TypeScript types generated via `make typecheck`.

**Tasks:**
- [ ] `app/main.py` — FastAPI factory, CORS, lifespan (start SimLoop)
- [ ] `app/ws.py` — WebSocket hub (broadcast typed messages)
- [ ] All `/api/*` route files (see section 5)
- [ ] `app/config.py` — Pydantic BaseSettings (seed, speed, db paths, etc.)
- [ ] `make typecheck` step: export OpenAPI JSON, run `@hey-api/openapi-ts`
- [ ] `tests/test_api.py` — contract tests for all REST endpoints

**Status:** [x] done

---

### M4 — Detectors, dataset generation, model training
**Intent:** Train the diagnosis ensemble and anomaly detectors on labelled episodes; produce `models/report.json`.

**Acceptance:** `make train` completes under 90 s (measured); all 7 fault classes achieve F1 > 0.7 (honest results, not hard-asserted); unseen faults excluded from training and calibration; `report.json` written.

**Training data integrity rules:**
- Cross-validation is group-wise by episode (never split within an episode).
- Where possible, hold out intensity levels from training and use them for evaluation.
- Symptoms intentionally overlap (cpu_stress vs traffic_surge; memory_leak vs memory_exhaustion; network_partition vs slow_dependency). F1 < 1.0 on confusable pairs is expected and honest.
- Unseen fault episodes are completely excluded. OOD threshold is derived only from leave-episode-out training distances.
- `make train` loops: generate episodes → check sample counts → if any class < minimum, generate more → warn loudly and continue or fail depending on config flag.

**Tasks:**
- [ ] `training/generate_dataset.py` — headless twin runs (no sleep), ≥30 episodes per known fault with intensity sweeps and onset variations; intensity-level hold-out splits; group-wise CV splits by episode ID; minimum sample check with auto-retry and loud warning
- [ ] `detection/rules.py` — threshold rules (CPU > 0.85, error_rate > 0.05, etc.)
- [ ] `detection/iforest.py` — scikit-learn IsolationForest wrapper, fit/predict/score, normalised to [0,1]
- [ ] `detection/autoencoder.py` — PyTorch autoencoder (input 64 → 32 → 16 → 32 → 64), reconstruction error normalised to [0,1] using 99th percentile of training errors
- [ ] `diagnosis/mlp_mcd.py` — PyTorch MLP with dropout enabled at inference (30 forward passes); uncertainty = entropy of mean distribution
- [ ] `diagnosis/xgb.py` — XGBoost multi-class classifier with SHAP attribution; permutation importance fallback if SHAP > 200 ms
- [ ] `diagnosis/evidence_rules.py` — deterministic rules: OOMKilled+restarts → MEMORY_EXHAUSTION/LEAK; NodeNotReady → NODE_FAILURE; error spike within 10 min of revision → BAD_DEPLOYMENT; high error rate + request_rate >> baseline → TRAFFIC_OVERLOAD; designed to overlap but not perfectly agree with ML
- [ ] `diagnosis/ensemble.py` — weighted ensemble (MLP + XGBoost + evidence rules); UNKNOWN abstention rule (§6)
- [ ] `training/train.py` — fit all models, derive OOD threshold (99th pct leave-episode-out Mahalanobis), derive τ_abstain, save artefacts to `models/`
- [ ] `training/evaluate.py` — confusion matrix, group-wise CV, detector comparison, unseen fault evaluation (20+ runs per fault per varied seed/intensity, report wrongly-AUTO rate + Wilson upper bound), write `report.json`; do not assert 100% ESCALATE rate — report honestly
- [ ] Unit tests: evidence rule coverage, feature shapes (exactly 64), model load/predict round-trip

**Status:** [ ] pending

---

### M5 — Trust signals, gate, runbook, executor, verification
**Intent:** Close the healing loop end-to-end; guardrail tests green.

**Acceptance:** memory_exhaustion AUTO-heals end-to-end in a headless test; cpu_overload returns ASK at S3; unseen fault returns ESCALATE (no hard assert — check that neither AUTO nor a known-label action fires); all guardrail tests green.

**Tasks:**
- [ ] `trust/signals.py` — compute U, S, N, H, E, D
- [ ] `trust/calibration.py` — leave-one-episode-out logistic fit + isotonic calibration
- [ ] `trust/gate.py` — fusion → P_correct → hard vetoes → gate decision
- [ ] `trust/action_policy.py` — risk class lookup, runbook ladder selection
- [ ] `healing/runbook.py` — per-diagnosis action ladder, retry counter
- [ ] `healing/guardrails.py` — allow-list, budget, cooldown, disruption budget, kill switch
- [ ] `healing/executor.py` — dry-run, audit write, execute via SimAdapter, kill switch check
- [ ] `healing/verifier.py` — wait 60s sim, health check, undo on worse, outcome label
- [ ] `tests/test_gate.py` — veto tests, threshold tests, autonomy stage tests
- [ ] `tests/test_executor.py` — allow-list, rate limit, cooldown, undo, kill switch

**Status:** [ ] pending

---

### M6 — Frontend foundation
**Intent:** React app wired to the backend with live state and a working Mission Control page.

**Acceptance:** `make dev` starts frontend; live clock ticks; tiles update on `tick` messages; narration feed scrolls; no console errors.

**Tasks:**
- [ ] Frontend scaffold: Vite + React 18 + TypeScript + Tailwind + shadcn/ui init
- [ ] `src/api/ws.ts` — typed WebSocket hook with reconnect, message dispatch
- [ ] `src/state/simStore.ts`, `incidentStore.ts`, `clusterStore.ts` — Zustand stores
- [ ] `src/components/layout/TopBar.tsx` — clock, speed, autonomy stage, kill switch button
- [ ] `src/components/layout/Sidebar.tsx` — page navigation
- [ ] `src/pages/MissionControl.tsx` — health score tile, node/pod/incident counts, MTTR, automation rate, active faults list, narration feed, incident list

**Status:** [ ] pending

---

### M7 — Topology view and Metrics Explorer
**Intent:** Visualise cluster state and metric history with fault/action markers.

**Acceptance:** Injecting cpu_stress causes a pod to animate red; a scale-up animates new pods appearing; metrics charts show baseline band and fault marker; no console errors.

**Tasks:**
- [ ] `src/components/topology/ClusterFlow.tsx` — React Flow canvas, nodes as containers, pods as cells, service edges
- [ ] `src/components/topology/NodeCard.tsx`, `PodCell.tsx`, `ServiceEdge.tsx` — visual states (phase colours, fault badge, wrench, pulsing ring)
- [ ] `src/pages/MetricsExplorer.tsx` — per-service/pod/node charts (ECharts/Recharts), baseline band, vertical markers (fault onset, action, recovery), detector score overlays
- [ ] Framer Motion animations: pod shake on fault, OOMKilled flash, pod spawn on scale-up, node grey-out

**Status:** [ ] pending

---

### M8 — Incident War Room
**Intent:** Show the full pipeline for a live incident with approve/reject.

**Acceptance:** Injecting memory_exhaustion shows all 7 stepper stages animate in order; approve button on cpu_overload triggers scale-up; all panels populated from real backend data.

**Tasks:**
- [ ] `src/components/incidents/PipelineStepper.tsx` — 7-stage horizontal stepper with timestamps
- [ ] `src/components/incidents/EvidencePanel.tsx` — hard signals + top SHAP features
- [ ] `src/components/incidents/DiagnosisPanel.tsx` — competing diagnosers side by side with class probabilities
- [ ] `src/components/incidents/TrustGatePanel.tsx` — P_correct gauge, U/S/N/H/E/D bars, veto indicators
- [ ] Decision card (AUTO/ASK/ESCALATE, risk class, why)
- [ ] Approve and Reject buttons (POST /api/incidents/{id}/approve|reject)
- [ ] Action log with guardrail ticks
- [ ] Verification curve (metric returning to baseline)
- [ ] `src/components/incidents/TriagePanel.tsx` — LLM/template triage text
- [ ] Reveal ground truth toggle
- [ ] Replay slider

**Status:** [ ] pending

---

### M9 — Digital Twin Studio and Control Center
**Intent:** Full UI control over simulation and autonomy. First fully demoable point.

**Acceptance:** Inject fault from UI, see it take effect; rewind and try different action; change autonomy stage; kill switch drops to recommend-only instantly.

**Tasks:**
- [ ] `src/pages/DigitalTwinStudio.tsx` — scenario library, fault injector form (fault type, target, intensity, onset shape, duration), chaos mode toggle, sim controls (play/pause/step/speed/reset/seed), snapshot/rewind, what-if branch
- [ ] `src/pages/ControlCenter.tsx` — autonomy stage selector (S0–S4), per-action mode table, threshold sliders, rate limits and cooldowns, allow-list display, kill switch, manual action console (dry-run preview + confirm), audit log with filters

**Status:** [ ] pending

---

### M10 — Unseen faults, Models and Trust page, LLM triage
**Intent:** Show that the Trust Gate behaves defensively on unseen faults; populate the reporting page with honest evaluation.

**Acceptance:** Evaluation runs ≥20 episodes per unseen fault across varied seeds and intensities; wrongly-AUTO rate + Wilson upper bound are reported in `report.json`; Models & Trust page renders from real data; LLM triage template works offline.

**Tasks:**
- [ ] Evaluation in `training/evaluate.py`: for each of 5 unseen faults, run ≥20 headless episodes, collect gate decisions; report wrongly-AUTO count, rate, and Wilson 95% upper confidence bound; write to `report.json.unseen_faults`; do NOT hard-assert all ESCALATE
- [ ] `src/pages/ModelsAndTrust.tsx` — confusion matrix heatmap, calibration plot, trust AUROC, unseen-fault table (wrongly-AUTO rate + CI), detector comparison, baselines vs TrustHeal
- [ ] `triage/templates.py` — deterministic template (offline, default): one-paragraph description, ranked hypotheses with confidence labels, evidence references, suggested runbook steps, command preview; checker rejects claims without evidence reference
- [ ] `triage/llm_triage.py` — provider interface, optional LLM backend via env vars, timeout to template, secret redaction; never used in AUTO decisions

**Status:** [ ] pending

---

### M11 — Compare Lab and Guided Demo (SHOULD)
**Intent:** Lockstep comparison + one-click demo.

**Acceptance:** Compare Lab runs same scenario in 3 arms (A0/A1/A2); MTTR table populated from real outcomes; guided demo overlay chains all 8 steps without errors; RNG stream isolation verified (same workload + fault sequences across arms).

**Compare Lab arm definitions:**
- **A0 (Kubernetes defaults):** probe restarts (CrashLoopBackOff) + HPA on CPU threshold. No TrustHeal pipeline.
- **A1 (Rule-based):** same health trigger, runbook table, executor, guardrails and verification as A2. Diagnosis from threshold rules + evidence rules only. No ML models. No Trust Gate — action fires if health is BAD and a runbook entry matches, with risk-class gate using a fixed conservative threshold.
- **A2 (TrustHeal):** full pipeline as described.
- Arms differ only in the diagnosis + gate component. Same seed, same fault schedule, independent per-arm healing that cannot perturb other arms' RNG.

**Tasks:**
- [ ] `twin/` — lockstep multi-instance support: same `SimLoop` tick driver, per-arm independent RNG streams (workload/fault_schedule/dynamics_noise), independent healing pipelines; test that arm A's scale-up does not change arm B's load-generator sequence
- [ ] `src/pages/CompareLab.tsx` — arm selector (A0/A1/A2, optional A3 RL), synchronised metric charts, MTTR counters, unsafe action count (actions that worsened the service), final summary table
- [ ] `src/components/demo/GuidedDemoOverlay.tsx` — step captions per demo script (§13), presenter mode (hides non-demo controls), "Run demo" button chains demo.json scenario steps automatically

**Status:** [ ] pending

---

### M12 — Polish, docs, optional extras
**Intent:** Everything works end-to-end; documentation complete; demo rehearsed.

**Acceptance:** All tests green; README has quickstart + architecture diagram + demo script; AGENTS.md current; KNOWN_GAPS documented; `make demo` runs without errors.

**Tasks:**
- [ ] README.md with quickstart, architecture diagram, demo script
- [ ] AGENTS.md updated with all commands and rules
- [ ] KNOWN_GAPS.md honest entries for anything not fully implemented
- [ ] All tests green (`make test`)
- [ ] Smoke check script passes (`scripts/smoke_check.py`)
- [ ] COULD: `adapters/kube_adapter.py` — read-only real adapter (Prometheus + k8s API, disabled by default)
- [ ] COULD: RL arm in Compare Lab (DQN/PPO placeholder)

**Status:** [ ] pending

---

## 11. Assumptions

1. **Python 3.11+ and Node 20+ are installed on the demo laptop.** `make dev` does not install these.
2. **SHAP** will be used for XGBoost feature attribution; if too slow during inference (> 200 ms), we fall back to permutation importance. SHAP is not used inside MC-dropout loops.
3. **MC-dropout** uncertainty is computed as the entropy of the mean class distribution over 30 forward passes, not mutual information. This is fast enough for inference.
4. **The 90-second cold-start budget** applies after `pip install` has already run. Dataset generation and training are the measured components. `make train` auto-generates extra episodes if minimum counts are not met, then warns loudly or fails based on a config flag.
5. **Compare Lab** lockstep uses three independent twin instances sharing the same `SimLoop` driver (same tick count, same fault schedule, different healing arms). Each arm has separate seeded RNG streams (workload, fault_schedule, dynamics_noise) so no arm's actions can perturb another arm's random sequence.
6. **Isotonic calibration** requires sufficient leave-one-episode-out samples. If insufficient for a risk class: gate defaults to ASK (not AUTO). If no calibrated gate exists at all: defaults to ESCALATE. Both cases are logged in KNOWN_GAPS.
7. **The autoencoder** is trained on NORMAL windows only. Its reconstruction error is normalised to [0,1] using the 99th percentile of training errors.
8. **Docker Compose** is provided but `make dev` is the primary demo path. Docker is for reproducibility.
9. **Symptom overlaps** between fault classes (cpu_stress↔traffic_surge, memory_leak↔memory_exhaustion, network_partition↔slow_dependency) are intentional. F1 < 1.0 on these pairs is honest and expected.
10. **Unseen fault evaluation** does not hard-assert 100% ESCALATE rate. It reports wrongly-AUTO rate with Wilson upper bound. The OOD threshold and τ_abstain are derived purely from training data and are never tuned on unseen faults.
11. **Signal H** is evaluated at decision time (not at incident open): H=1 if health is still BAD and diagnosis is a fault class; H=0 if health has recovered or diagnosis is NORMAL.

---

## 12. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Training > 90 s cold start | Medium | Headless data gen is fast; use small models; cache artefacts; measure and prune |
| MC-dropout too slow at inference | Low | Cap at 30 passes; run on CPU is < 50 ms for a 128-unit MLP |
| SHAP too slow at inference | Medium | Permutation importance fallback (already in plan) |
| React Flow performance with 50+ pods | Low | Virtualise off-screen nodes; memoize cells |
| WebSocket backpressure at 30x speed | Medium | Debounce tick messages to frontend (max 10 Hz); batch cluster events |
| Calibration data insufficient for R2 targets | Medium | Default to ASK; document in KNOWN_GAPS |
| Framer Motion + ECharts at 60 fps | Low | Limit animated elements; use canvas-mode charts |

---

## 13. Non-Goals

- No authentication or multi-user support.
- No real cluster access by default (KubeAdapter is COULD, disabled, read-only).
- No persistent telemetry across restarts (ring buffer is in-memory).
- No cloud deployment (no Kubernetes manifests, no cloud CI).
- No mobile / responsive layout (desktop operations-centre only).

---

## 14. Implementation Order

M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → M9 → M10 → M11 → M12

Each milestone is self-contained: tests and a smoke check run before the next begins.

---

## 15. Sub-Task Implementation Instructions (for Agent mode)

When switching to Agent mode, process one milestone at a time using `start_subtask`. Each subtask prompt should include:

> "Read `plans/00-master-plan.md` section 10 milestone M{N}. Implement all tasks listed. Run `pytest` and the smoke check. Update `plans/00-master-plan.md` M{N} status to done. Update AGENTS.md and CHANGELOG.md."

After each milestone completes and the user approves, move to the next.
