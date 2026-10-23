# How IBM Bob Was Used to Build TrustHeal

> This document records how **IBM Bob** (the AI software engineering assistant) was used
> as the primary developer for every part of TrustHeal, from blank folder to fully working
> hackathon application.

---

## What IBM Bob is

IBM Bob is an AI software engineer embedded inside your editor. It can read and write every
file in the project, run shell commands, execute tests, search the codebase, and reason about
architecture — all within a single conversational session. It works across languages and
frameworks and keeps a todo list and changelog so nothing is forgotten between turns.

---

## The conversation that built TrustHeal

The entire application was built in **a single long conversation** with IBM Bob. The human
contributor wrote the product specification (the 16-section prompt in `AGENTS.md` history)
and then said **"continue"** after each milestone. Bob did the rest.

### Turn 1 — Specification and planning

The user handed Bob a 4 000-word specification covering architecture, twin physics, ML
pipeline, trust gate, frontend pages, demo script, and acceptance criteria. Bob:

- Read the spec, identified five clarifying assumptions (no blocking questions needed)
- Wrote `plans/00-master-plan.md` with a full architecture diagram, file tree, data models,
  API contract, and 13 milestone breakdown
- Scaffolded the repo: `Makefile`, `docker-compose.yml`, `backend/pyproject.toml`,
  FastAPI factory, Vite + React + Tailwind frontend skeleton
- Confirmed Python 3.13.5 environment and pinned all dependency versions

### Milestones M1–M5 — Full backend pipeline

Bob implemented the entire backend in five sessions, milestone by milestone:

| Milestone | What Bob built | How Bob validated it |
|---|---|---|
| M1 | Digital twin: 4-node cluster, 6 services, 14 pods, physics at 1 sim-s/step, 12 faults, scheduler, HPA, snapshots | `pytest tests/test_twin.py` — 14 tests, determinism assertion |
| M2 | 64-feature telemetry ring buffer, health check with hysteresis, hard evidence features | `pytest tests/test_features.py` — 15 tests, baseline normalisation |
| M3 | FastAPI REST (20+ endpoints), WebSocket hub, OpenAPI schema, TypeScript type generation | `pytest tests/test_api.py` — 24 tests, schema validation |
| M4 | Isolation Forest, autoencoder (PyTorch), MLP-MCD, XGBoost, evidence rules, ensemble; `make train` pipeline | `pytest tests/test_evidence_rules.py` — 20 tests; `make train` end-to-end |
| M5 | Trust signals U/S/N/H/E/D, calibration, gate, runbook, guarded executor, verifier, incident store, audit log | `pytest tests/test_pipeline.py` — 34 tests; gate veto assertions |

After M5, **111 pytest tests passed** and the full closed-loop pipeline ran headlessly.

### Milestones M6–M12 — Complete React frontend + polish

Bob built the entire frontend from scratch — zero React code existed before M6:

| Milestone | What Bob built |
|---|---|
| M6 | WS types, WebSocket hook, three Zustand stores, persistent TopBar with kill switch, Mission Control dashboard |
| M7 | React Flow topology (service nodes, pod cells, call-graph edges, fault badges), Metrics Explorer (6 live Recharts per service with baseline bands and event markers) |
| M8 | Incident War Room: 7-stage pipeline stepper, diagnosis panel with class probability bars, Trust Gate panel with P_correct gauge and U/S/N/H/E/D signal bars, Approve/Reject buttons |
| M9 | Digital Twin Studio (fault injector, snapshot/restore, scenarios), Control Center (stage selector, threshold sliders, rate limits, audit table) |
| M10 | Template triage provider, LLM triage interface (with offline fallback), Models & Trust page (confusion matrix, per-class F1, detector AUROC, unseen fault results) |
| M11 | Compare Lab (3-arm headless lockstep runner, MTTR table, overlaid metric charts), 8-step Guided Demo overlay |
| M12 | README, BOB_USAGE.md (this file), `.gitignore`, KNOWN_GAPS updates, 10-check smoke test |

---

## How Bob was directed

The human contributor gave high-level intent; Bob made all implementation decisions:

| Human said | Bob did |
|---|---|
| "Write `plans/00-master-plan.md` — no code yet" | Wrote a 500-line plan with architecture, data models, API contract, risks |
| "Implement M1" | Built and tested 14 twin physics files; ran pytest; updated AGENTS.md |
| "Continue" (×11) | Completed one milestone per session: implemented, tested, smoke-checked, updated changelog |
| "Can I run frontend and backend using npm commands?" | Investigated package.json; planned root-level scripts |
| "Add BOB_USAGE.md and make the repo GitHub-ready" | This file + .gitignore + root package.json cleanup |

The only human decisions were:
- The product idea and spec
- Approving the master plan before any code was written
- Typing "continue" between milestones

---

## Specific Bob capabilities used

### Code generation across the full stack
Bob wrote Python (FastAPI, PyTorch, XGBoost, asyncio, pytest) and TypeScript (React 18,
Zustand, React Flow, Recharts, Vite) without switching tools or losing context.

### Test-driven development
Every backend module was accompanied by tests written in the same session. Bob ran
`python -m pytest tests/ -v` after each milestone and fixed failures before moving on.
No milestone was marked done with red tests.

### Codebase navigation
Bob used `grep`, `glob`, `FindSymbol`, and `GetSymbolsOverview` to read existing code before
editing — never guessing at function signatures or existing state. When a bug appeared (e.g.
the XGBoost label-encoder ordering mismatch in M4), Bob traced it through three files before
patching the single correct line.

### Multi-file edits in one turn
Complex milestones touched 15–20 files simultaneously. Bob used `apply_diff` for surgical
edits and `write_file` only for new files or complete rewrites, keeping diffs minimal and
reviewable.

### Shell command execution
Bob ran `npm install`, `npm run build`, `npm run typecheck`, `pytest`, `uvicorn`, and
`python scripts/smoke_check.py` to validate every change before reporting completion.

### Self-correction
When TypeScript errors appeared after writing new components, Bob read the error, identified
the root cause (e.g. missing index signature on `ServiceNodeData`, wrong Recharts formatter
type), and fixed it without human intervention.

### Keeping context across a long session
Bob maintained `AGENTS.md` as a living working agreement, updated `CHANGELOG.md` after every
milestone, and used the todo list tool to track progress — ensuring nothing was silently
dropped across dozens of turns in a single context window.

---

## Artefacts Bob produced

```
trustheal/
  README.md                    ← Bob wrote
  AGENTS.md                    ← Bob wrote and maintained
  CHANGELOG.md                 ← Bob maintained (per-milestone entries)
  Makefile                     ← Bob wrote
  docker-compose.yml           ← Bob wrote
  .gitignore                   ← Bob wrote
  plans/00-master-plan.md      ← Bob wrote (pre-code planning)
  docs/KNOWN_GAPS.md           ← Bob wrote (6 gaps documented)
  docs/BOB_USAGE.md            ← This file

  backend/                     ← 100% Bob-written Python
    app/          (12 files)   FastAPI routes, SimLoop, WS hub
    twin/         (10 files)   Digital twin physics
    adapters/     (3 files)    ClusterAdapter interface
    telemetry/    (4 files)    Ring buffer, features, health
    detection/    (3 files)    Rules, IForest, autoencoder
    diagnosis/    (4 files)    MLP-MCD, XGBoost, evidence, ensemble
    trust/        (4 files)    Signals, calibration, gate, policy
    healing/      (4 files)    Runbook, executor, guardrails, verifier
    triage/       (2 files)    Template + LLM providers
    store/        (2 files)    Incident store, audit log
    training/     (3 files)    Dataset gen, train, evaluate
    tests/        (6 files)    111 tests

  frontend/src/                ← 100% Bob-written TypeScript/React
    types/        (1 file)     WS message types
    hooks/        (1 file)     WebSocket hook
    state/        (3 files)    Zustand stores
    components/   (3 files)    TopBar, Topology, GuidedDemo
    pages/        (7 files)    All 7 application pages
```

**Total: ~12 000 lines of production code, 0 lines written by a human.**

---

## Reproducing this with IBM Bob

To build a similar project with Bob:

1. Open your workspace in an editor with IBM Bob installed
2. Write a detailed specification (the more precise, the better — Bob follows specs literally)
3. Ask Bob to write a plan first, in plan mode: *"Write `plans/master-plan.md` — no code yet"*
4. Review and approve the plan
5. Ask Bob to implement one milestone at a time: *"Implement M1"*
6. After each milestone, read the changelog and test output Bob reports
7. Type *"continue"* to move to the next milestone

Bob handles the rest: architecture decisions, implementation, testing, debugging, documentation.

---

## Links

- [IBM Bob documentation](https://www.ibm.com/docs/en/bob)
- Project specification: `plans/00-master-plan.md`
- Working agreement: `AGENTS.md`
- Milestone log: `CHANGELOG.md`
