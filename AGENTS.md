# TrustHeal — AGENTS.md

> Working agreement for all agents. Keep this file current after every milestone.

---

## Architecture

TrustHeal is a trust-aware, explainable self-healing system for Kubernetes running
entirely on a laptop with no cloud account and no real cluster.

```
backend/   Python 3.11 · FastAPI · Uvicorn
  app/       FastAPI factory, REST routes, WebSocket hub
  twin/      Digital twin: physics, faults, scheduler, HPA, snapshots
  adapters/  ClusterAdapter ABC, SimAdapter (twin), KubeAdapter (COULD, disabled)
  telemetry/ Ring buffer, feature engine (64d), evidence features, health check
  detection/ Threshold rules, Isolation Forest, autoencoder
  diagnosis/ MLP-MCD, XGBoost, evidence rules, ensemble
  trust/     Signals (U/S/N/H/E/D), calibration, gate, action policy
  healing/   Runbook, guarded executor, guardrails, verifier
  triage/    Template triage (default), LLM triage (optional)
  store/     Incident store (SQLite file), audit log (SQLite file)
  training/  Headless dataset generation, train, evaluate → models/report.json
  tests/     pytest suite

frontend/  React 18 · TypeScript · Vite · Tailwind CSS
  src/api/   @hey-api/openapi-ts generated client + typed WS hook
  src/state/ Zustand stores (sim, incidents, cluster)
  src/pages/ 7 pages (Mission Control, Metrics, War Room, Twin Studio,
             Control Center, Models & Trust, Compare Lab)
  src/components/ Layout, topology (React Flow), charts, incident panels, demo overlay

models/    Trained artefacts (gitignored binaries) + report.json
plans/     Master plan and milestone breakdowns
docs/      KNOWN_GAPS.md
scripts/   start_dev.sh, smoke_check.py
```

## Key Design Decisions

| Decision | Choice |
|---|---|
| Physics tick rate | 1 sim-s per physics step; telemetry every 15 sim-s; UI batched at ~10 Hz wall |
| Simulation loop | asyncio background task (demo) + step_physics/step_telemetry API (tests) |
| Calibration fallback | insufficient data → ASK; no gate at all → ESCALATE |
| Persistence | File SQLite for incidents+audit; in-memory ring buffer for telemetry |
| TypeScript types | @hey-api/openapi-ts from FastAPI OpenAPI schema |
| Compare Lab arms | A0=k8s defaults, A1=rule-based (same executor/guardrails, no ML/gate), A2=TrustHeal |
| RNG streams | workload / fault_schedule / dynamics_noise — separate seeded streams per arm |
| Feature vector | Exactly 64 features: 9 metrics × 5 stats + 19 context features |
| Signal H | Evaluated at decision time: 1 if still BAD + fault class; 0 if recovered or NORMAL |
| UNKNOWN | Abstention rule: max(probs) < τ_abstain OR (MLP≠XGB AND evidence silent) |
| Unseen faults | Evaluation reports wrongly-AUTO rate + Wilson CI; no hard-assert |

## Milestones

| ID | Status | Notes |
|---|---|---|
| M0 | ✅ done | Skeleton, health endpoint, frontend scaffold — Python 3.13 + existing env confirmed |
| M1 | ✅ done | Twin core — 18/18 tests pass |
| M2 | ✅ done | Telemetry, features (64d), health check hysteresis — 57 tests |
| M3 | ✅ done | FastAPI REST + WebSocket — 57 tests |
| M4 | ✅ done | Detectors, ensemble, dataset gen, train, evaluate — 77 tests; XGB 97% train acc; F1=0.70 held-out |
| M5 | ✅ done | Trust signals (U/S/N/H/E/D), gate, runbook, executor, verifier, incident store, audit — 111 tests |
| M6 | ✅ done | Frontend foundation: routing, WS hook, Zustand stores, TopBar, Mission Control + 6 page stubs |
| M7 | ✅ done | Topology (React Flow, live service nodes + call-graph edges) + Metrics Explorer (6 live charts/service with markers) |
| M8 | ✅ done | Incident War Room: pipeline stepper, diagnosis panel, Trust Gate breakdown, approve/reject, action log, verification |
| M9 | ✅ done | Twin Studio: topology + fault injector + snapshots + scenarios; Control Center: stage, thresholds, limits, audit; models_report endpoint |
| M10 | ✅ done | Triage (template provider + LLM interface), Models & Trust page (CM, F1, detectors, unseen faults), models_report endpoint |
| M11 | ✅ done | Compare Lab (3-arm lockstep, isolated worker threads, repeatability test, MTTR table, metric charts) + Guided Demo overlay |
| M12 | ✅ done | README, known gaps, smoke check (10 checks); unseen-fault evaluation is implemented but still records false AUTO actions (GAP-005) |

## M4 Artefacts

| File | Purpose |
|---|---|
| `backend/detection/rules.py` | Threshold rule detector (module-level score/is_anomalous) |
| `backend/detection/iforest.py` | IsolationForest wrapper (fit/score/save/load) |
| `backend/detection/autoencoder.py` | PyTorch AE 64→32→16→32→64 (fit/score/save/load) |
| `backend/diagnosis/evidence_rules.py` | Deterministic rules diagnoser (diagnose/confidence) |
| `backend/diagnosis/mlp_mcd.py` | MC-dropout MLP 64→256→128→64→9 (predict_proba_mc) |
| `backend/diagnosis/xgb.py` | XGBoost classifier with SHAP fallback; label-encoder fixed |
| `backend/diagnosis/ensemble.py` | Weighted MLP(0.45)+XGB(0.45)+EV(0.10) blend; UNKNOWN abstention |
| `backend/training/generate_dataset.py` | 240 episodes × 715 sim-s; 28800 labelled samples |
| `backend/training/train.py` | Fit detectors → classifiers → tau_abstain → OOD threshold |
| `backend/training/evaluate.py` | Stratified held-out eval plus separate unseen-fault Trust Gate evaluation → models/report.json |
| `models/dataset.npz` | 28800 samples, 8 classes |
| `models/mlp_mcd.pt` | Trained MLP (60 epochs) |
| `models/xgb.json` | Trained XGBoost (200 trees) |
| `models/iforest.joblib` | Trained IsolationForest |
| `models/autoencoder.pt` | Trained autoencoder |
| `models/thresholds.json` | tau_abstain, OOD Mahalanobis threshold |
| `models/report.json` | Evaluation report (CM, F1, detectors, calibration) |

## Commands

```bash
# Install dependencies
make install

# Start backend + frontend (dev mode)
make dev

# Run all tests
make test

# Train models (headless; CPU training/evaluation can take several minutes)
make train

# Start in demo/presenter mode
make demo

# Generate TypeScript types from OpenAPI schema
make typecheck

# Backend only
cd backend && uvicorn app.main:app --reload --port 8000

# Frontend only
cd frontend && npm run dev

# Pytest (backend)
cd backend && python -m pytest tests/ -v

# Check types (backend)
cd backend && python -m mypy app/ --ignore-missing-imports
```

## Rules

1. **Never fake results.** Every number, chart and decision in the UI must come from the backend pipeline. No hard-coded demo outputs.
2. **No destructive access to real systems.** KubeAdapter is read-only and disabled by default.
3. **Same seed + scenario = same telemetry.** Determinism is tested.
4. **The pipeline only ever talks to the ClusterAdapter interface** — never to twin internals directly.
5. **One open incident per service** at any time.
6. **Executor is the only component that may change the simulated cluster.**
7. **Audit log is written before execution** — never after.
8. **UNKNOWN label always escalates.** No action on UNKNOWN.
9. **Unseen faults are never used for training or calibration.** OOD threshold derived from training data only.
10. **Feature vector is exactly 64 elements.** Any change must update both features.py and all model artefacts.
11. **Type hints on all public functions.** Pydantic models for all API payloads.
12. **If something cannot be finished, add an entry to docs/KNOWN_GAPS.md.** No silent stubs.
13. **Tests must pass before merging a milestone.** Run `make test` after each milestone.

## Ports

| Service | Port |
|---|---|
| FastAPI (Uvicorn) | 8000 |
| Vite dev server | 5173 |
| WebSocket | ws://localhost:8000/ws |

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| TRUSTHEAL_SEED | 42 | Global RNG seed |
| TRUSTHEAL_SPEED | 10 | Default sim speed multiplier |
| TRUSTHEAL_DB_PATH | ./data/trustheal.db | SQLite file path |
| TRUSTHEAL_MODELS_DIR | ./models | Trained artefact directory |
| TRUSTHEAL_LOG_LEVEL | INFO | Log level |
| TRUSTHEAL_LLM_PROVIDER | template | LLM provider (template/openai/ollama) |
| TRUSTHEAL_LLM_URL | — | LLM API base URL (if not template) |
| TRUSTHEAL_LLM_API_KEY | — | LLM API key (redacted in logs) |
| TRUSTHEAL_TRAIN_FAIL_ON_LOW_SAMPLES | false | Fail make train if min samples not met |
