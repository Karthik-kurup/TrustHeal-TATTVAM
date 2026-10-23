# Known Gaps

> This file records honestly any MUST items that were not fully implemented.
> No silent stubs — if something is here, there is a clearly marked placeholder in code.

---

*Updated after M12 and the UI/runtime reliability pass.*

## GAP-001: make train > 90 second target

**Milestone:** M4
**Priority:** SHOULD
**Status:** partial

**What is missing:**
Full `make train` (dataset gen + MLP/XGB training + eval) takes ~250s on CPU-only.
Target in master plan is < 90s.

**Why:**
XGBoost training on 28800 × 64 features with n_estimators=200 takes ~180s.
MLP 60 epochs on CPU takes ~30s.
No GPU in the dev environment.

**Where the stub is:**
`backend/training/train.py` — no explicit optimisation; trains sequentially.

**Impact:**
`make train` is slow on first run. Cached artefacts mean subsequent runs skip training.
The demo does not require re-training (models are pre-cached).

**Mitigation:**
Run `python -m training.train --skip-gen` after first generation to avoid re-generating the dataset.
Reduce `n_estimators` to 100 or `epochs` to 30 if faster training is needed for a demo setup.

---

## GAP-002: Separate unseen-fault evaluation set (M10)

**Milestone:** M10
**Priority:** SHOULD
**Status:** resolved

Unseen-fault data is now generated separately from the training dataset, and the evaluator sends
balanced samples through diagnosis, runbook selection, and the real Trust Gate. The data remains
excluded from training and calibration. See GAP-005 for the unresolved safety result.

---

## GAP-003: NODE_FAILURE and NORMAL class F1 below target (M4)

**Milestone:** M4
**Priority:** SHOULD
**Status:** partial

**What is missing:**
NODE_FAILURE F1 = 0.44, NORMAL F1 = 0.38 on stratified held-out split.
Target: F1 >= 0.70 for all classes (implicit from master plan quality section).

**Why:**
NODE_FAILURE produces cascading effects that overlap with BAD_DEPLOYMENT and CPU_OVERLOAD
signatures. NORMAL episodes overlap with early-stage faults in the feature window.
MLP is only trained for 60 epochs; more epochs would improve both.

**Where the stub is:**
No code stub — this is a model quality limitation.

**Impact:**
NODE_FAILURE may be misdiagnosed as another fault class; the Trust Gate's E and D signals
will disagree and escalate, partially compensating for the lower recall.

---

## Template

```
## GAP-XXX: <Short title>

**Milestone:** MN
**Priority:** MUST / SHOULD / COULD
**Status:** stub / partial / blocked

**What is missing:**
<Description of what was not implemented>

**Why:**
<Reason — time, complexity, dependency>

**Where the stub is:**
`path/to/file.py` — function `foo()` raises `NotImplementedError("GAP-XXX")`

**Impact:**
<What will fail or be degraded until this is fixed>
```

## GAP-004: OOD veto fires frequently in live simulation (M5)

**Milestone:** M5
**Priority:** SHOULD
**Status:** partial

**What is missing:**
The Mahalanobis OOD threshold (28.8) was derived from training samples which have
different per-feature distributions than live simulation telemetry.
In live runs, N >= 0.95 fires for most incidents, triggering VETO_OOD and ESCALATE
even for clearly known faults.

**Why:**
Training features are collected from headless single-fault episodes (uniform fault
for entire window), while live features include noisy mixed-state windows.
The p99 threshold from training is too tight for live data.

**Where the stub is:**
`backend/trust/signals.py` -- compute_N() uses the p99 threshold directly.

**Impact:**
The system escalates instead of auto-healing in many cases where it could act.
For the demo, the Trust Gate will show ESCALATE with VETO_OOD even for known
faults. This is visible and narrated, but not the intended demo behaviour.

**Mitigation:**
No threshold has been changed based on unseen evaluation data. The mismatch needs a separate
calibration study using permitted known/normal data; the current OOD veto remains a known
quality limitation.

---

## GAP-005: Unseen faults can still trigger automatic actions

**Milestone:** M10
**Priority:** MUST
**Status:** partial

**What is missing:**
The unseen evaluation set is now generated separately and evaluated through the real diagnosis and
Trust Gate path, but the wrongly-automated rate is not zero. A diagnostic run of 100 samples from
the seed-50000 unseen dataset produced 38 AUTO decisions (38%; Wilson 95% CI 29.1%-47.8%).
This violates the intended safety requirement. The standard report now samples five examples per
fault type to bound evaluation time; its latest result is recorded in `models/report.json`.

**Why:**
The classifier can confidently map unseen fault signatures to known classes. UNKNOWN abstention
alone cannot protect cases that are misclassified as known, and current OOD/trust signals do not
veto every such decision. Unseen samples are evaluation-only and must not be used to tune or
calibrate the model.

**Where the stub is:**
`backend/training/evaluate.py` — `_evaluate_unseen_gate()` records actual gate decisions.

**Impact:**
The system is not safe for autonomous use on real systems. Keep it simulation-only and do not claim
that unseen-fault automation is prevented. The Models & Trust page exposes the measured rate and
confidence interval.

**Mitigation:**
This remains open. Improve detection using training/validation data that complies with the
no-unseen-training/calibration rule, then verify on a new, untouched unseen evaluation set.

---

## GAP-006: Compare Lab arm determinism

**Milestone:** M11
**Priority:** SHOULD
**Status:** resolved

Each arm now runs in an isolated worker thread with its own seeded simulation state. A regression
test confirms identical metric series for the same arm, seed, and schedule.
