# TrustHeal

**Trust-aware, explainable self-healing for Kubernetes — runs entirely offline on a laptop.**

TrustHeal demonstrates that safe autonomous healing requires more than a correct diagnosis.
Between diagnosis and action sits a **Trust Gate** that weighs MC-dropout uncertainty,
attribution stability, out-of-distribution novelty, health agreement, evidence agreement, and
diagnoser agreement into a calibrated probability. Only when that probability clears the
threshold does the gate approve an automatic action. Otherwise it asks a human or escalates
with an explanation. Preventing autonomous actions on unseen faults is a safety requirement,
but the current evaluation still finds false automatic actions; see the live evaluation report
and [known gaps](docs/KNOWN_GAPS.md). This project is a local simulation, not a production
Kubernetes controller.

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/yourname/trustheal && cd trustheal

# 2. Install all dependencies (Python + npm)
make install

# 3. Train the models (generates training and separate unseen-evaluation datasets)
make train       # CPU training/evaluation can take several minutes

# 4. Start backend + frontend
make dev         # FastAPI on :8000, Vite on :5173

# 5. Open http://localhost:5173
```

### One-command demo (presenter mode)

```bash
make demo        # Starts at 10x speed with S3 autonomy; guided demo overlay ready
```

### Run tests

```bash
make test        # Runs the backend pytest suite
```

### Start on Windows without GNU Make

Open two PowerShell terminals from the project folder:

```powershell
# Terminal 1
Set-Location .\backend
python -m uvicorn app.main:app --reload --port 8000
```

```powershell
# Terminal 2
Set-Location .\frontend
npm run dev
```

Then open <http://localhost:5173>.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         TrustHeal — System Overview                      │
├──────────────┬──────────────────────────────────┬───────────────────────┤
│  Frontend    │        Backend pipeline           │    Digital Twin       │
│  React 18    │                                   │                       │
│  TypeScript  │  ┌─────────┐   ┌──────────────┐  │  ┌────────────────┐  │
│  Vite        │  │Telemetry│   │  Detection   │  │  │ ClusterState   │  │
│  Tailwind    │  │ Store   │──▶│  Rules+IF+AE │  │  │ 4 nodes        │  │
│              │  └────┬────┘   └──────────────┘  │  │ 6 services     │  │
│  Pages:      │       │                           │  │ 14 pods        │  │
│  Mission     │  ┌────▼────────────────────────┐  │  └──────┬─────────┘  │
│  Control     │  │   Diagnosis Ensemble         │  │         │            │
│  Metrics     │  │   MLP-MCD + XGB + EvidRules  │  │  ┌──────▼─────────┐  │
│  War Room    │  └────────────┬────────────────┘  │  │  Dynamics      │  │
│  Twin Studio │               │                   │  │  HPA / Sched.  │  │
│  Control     │  ┌────────────▼────────────────┐  │  │  Faults        │  │
│  Models      │  │       Trust Gate             │  │  └────────────────┘  │
│  Compare Lab │  │  U · S · N · H · E · D       │  │                       │
│              │  │  P_correct → AUTO/ASK/ESC    │  │  SimAdapter           │
│  Components: │  └────────────┬────────────────┘  │  (ClusterAdapter ABC) │
│  Topology    │               │                   │                       │
│  TopBar      │  ┌────────────▼────────────────┐  │                       │
│  IncidentWR  │  │  Executor + Guardrails       │  │                       │
│  GuidedDemo  │  │  Runbook · Verifier · Audit  │  │                       │
└──────────────┴──────────────────────────────────┴───────────────────────┘
                         │
                    WebSocket /ws
                    REST /api/*
                         │
               ┌─────────▼────────────┐
               │    Zustand stores    │
               │  simStore            │
               │  incidentStore       │
               │  clusterStore        │
               └──────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.13, FastAPI, Uvicorn, Pydantic v2 |
| ML | scikit-learn, XGBoost, PyTorch (CPU), SHAP |
| Storage | SQLite (SQLModel), in-memory ring buffers |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS |
| Visualisation | Recharts, React Flow (@xyflow/react) |
| State | Zustand, TanStack Query |
| Transport | REST + WebSocket |

---

## Demo Script (~6 minutes at 10x speed)

1. **Steady state** — healthy cluster, baselines, narration feed
2. **Memory exhaustion on checkout** — inspect the response-plan dialog, then AUTO restart, full pipeline, recovery in <1 min wall time
3. **CPU overload on frontend** — gate returns ASK; response-plan dialog shows proposed steps and alternatives before approve/reject; scale-up + verification
4. **Bad deployment on payment** — evidence + ML agree; rollback proposed; error rate returns to baseline
5. **Unseen fault (dns_failure)** — diagnosis looks confident, but N and OOD veto → ESCALATE; triage explains
6. **Node failure** — pods move; reschedule requires approval
7. **Kill switch** — flip mid-incident, system drops to recommend-only instantly
8. **Compare Lab** — same scenario, no healing vs rules vs TrustHeal, MTTR + unsafe action table

Click **▶ Run Demo** in the top bar to chain all 8 steps with on-screen captions.

---

## File Layout

```
trustheal/
  AGENTS.md              Working agreement and milestone status
  CHANGELOG.md           Per-milestone changelog
  Makefile               dev | install | train | test | demo
  README.md              This file
  plans/
    00-master-plan.md    Approved master plan
  docs/
    KNOWN_GAPS.md        Honestly documented known gaps
  backend/
    app/                 FastAPI factory, REST routers, WebSocket hub, SimLoop
    twin/                Digital twin: physics, faults, scheduler, HPA, snapshots
    adapters/            ClusterAdapter ABC, SimAdapter
    telemetry/           Ring buffer, 64-feature engine, health check
    detection/           Rules, Isolation Forest, autoencoder detectors
    diagnosis/           MLP-MCD, XGBoost, evidence rules, ensemble
    trust/               U/S/N/H/E/D signals, calibration, gate, action policy
    healing/             Runbook, guarded executor, guardrails, verifier
    triage/              Template + LLM triage provider
    store/               Incident store, audit log
    training/            Dataset generation, train, evaluate → models/report.json
    tests/               111 pytest tests
  frontend/
    src/
      types/ws.ts        Typed WS message definitions
      hooks/useWs.ts     WebSocket hook
      state/             Zustand stores (simStore, incidentStore, clusterStore)
      components/        TopBar, Topology, GuidedDemo
      pages/             MissionControl, MetricsExplorer, IncidentWarRoom,
                         TwinStudio, ControlCenter, ModelsAndTrust, CompareLab
  models/                Trained artefacts (gitignored binaries)
```

---

## Key Concepts

### Trust Gate
The gate sits between diagnosis and action. It computes six trust signals:
- **U** — MC-dropout uncertainty (30 forward passes)
- **S** — Attribution stability under small perturbations
- **N** — Mahalanobis OOD distance (normalised)
- **H** — Health check agreement
- **E** — Evidence-ML agreement (1=confirm, 0.5=silent, 0=contradict)
- **D** — MLP vs XGB diagnoser agreement

A logistic calibration model fuses them into P_correct. Hard vetoes (OOD, E=0, kill switch, UNKNOWN) override the gate unconditionally.

### Autonomy Stages
| Stage | Mode |
|---|---|
| S0 | Shadow — observe only |
| S1 | Recommend — narrate proposed actions |
| S2 | Approve — every action needs human approval |
| S3 | Auto Low-risk — R0/R1 automatic, R2+ need approval |
| S4 | Auto Medium-risk — R0/R1/R2 automatic, R3 needs approval |

Demo starts at **S3**.
Control Center changes apply to the running simulation immediately and are held in memory
(they reset when the API server restarts). Pending ASK actions are rechecked against the current
stage, kill switch, and proposed action before they can execute.

### Unseen Faults
Five unseen fault types (pod_failure, compound, dns_failure, slow_dependency, disk_io_pressure)
are never used during training or calibration. The evaluation report measures the wrongly-automated
rate (target: 0%). UNKNOWN always escalates, but the model can misclassify an unseen fault as a
known class; the measured false-AUTO rate is nonzero. Treat the report as a warning, not a safety
certification, and keep autonomy disabled for any real system.

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `TRUSTHEAL_SEED` | 42 | RNG seed |
| `TRUSTHEAL_SPEED` | 10 | Default sim speed multiplier |
| `TRUSTHEAL_LOG_LEVEL` | INFO | Log level |
| `TRUSTHEAL_LLM_PROVIDER` | template | Triage provider (template/openai/ollama) |
| `TRUSTHEAL_LLM_URL` | — | LLM API base URL |
| `TRUSTHEAL_LLM_API_KEY` | — | LLM API key (redacted in logs) |
| `TRUSTHEAL_LLM_MODEL` | llama3.2 | LLM model name |

---

## Known Gaps

See [`docs/KNOWN_GAPS.md`](docs/KNOWN_GAPS.md) for honestly documented limitations.
