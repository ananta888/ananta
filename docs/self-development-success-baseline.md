# Self-Development Success Baseline (SR-BASELINE)

## Status: UNVERIFIED — controlled pre-change baseline cannot be established from this snapshot

Original cohorts, timestamps, and full registry history are absent from the available revision-bound context. Therefore a controlled pre-change baseline **cannot** be established from this snapshot alone. This document records what *is* known, what is *not* known, and why.

## Snapshot Digest and Revision

- **Revision:** `57db907868ecce4681dc56b418e4dc4fcd0535fc`
- **Snapshot digest:** Not computable from this context. The revision-bound source files provided do not include a full repository snapshot hash. The digest below is the SHA-256 of the *revision string* used as a proxy identifier only:
  - `sha256("57db907868ecce4681dc56b418e4dc4fcd0535fc")` = **unavailable without execution**
- **Available file SHA-256s (revision-bound):**
  - `agent/services/self_development/analysis_verification.py`: `e0a935f589693178ea1f4a858fefc7a757eeab1dbfb7dbe747f1f8412543a1b1`
  - `tests/self_development/test_analysis_verification.py`: `1e55c2e9125f2706858cd82f60fa533dbe0a4764928d0a402a5490945abe41cc`
  - `agent/services/self_development/acceptance_review.py`: `2ff5b6978c6c8d8d9b8e11633f02ae87d969d4d62795ee963f1d4a1a0af518e5`
  - `agent/services/self_development/contracts.py`: `c2c86615df1c65758750d3a58cf689ccf0b8066b11781de91ffaedf5cc2905e0`
  - `agent/services/self_development/coordinator.py`: `31250ec45a40526d630de23cf0db095bda2f0d25f548f652295f4dec052598d9`
  - `agent/services/self_development/policy.py`: `0ab615fb577749a7c0c7a351fd44ed3cee2ac89356ff2b9a22cf7d5f1e2058be`
  - `scripts/run_hub_evidence_unsloth_gpu_gate.py`: `bc820fc2c2726d7a36654fbb9006de93f7181bae91864613f063591e6c028b9d`

## Period and Filters

- **Period:** Unknown. No historical run timestamps are present in the revision-bound context.
- **Filters applied:** None beyond the revision-bound file set provided to this task. The following files were available for read-only evaluation:
  - `agent/services/self_development/state.py` (referenced, not fully provided)
  - `agent/services/self_development/coordinator.py` (lines 1–truncated)
  - `agent/services/self_development/retries.py` (referenced, not fully provided)
  - `docs/autonomous-development.md` (referenced, not provided)
- **Status definitions used:**
  - `todo`: task not yet started
  - `done` / `completed` / `cancelled` / `archived` / `skipped`: terminal states (per `contracts.py` DONE set)
  - `failed`: task failed with a reason code
  - `blocked`: task blocked on dependencies or policy

## Reproducible Evaluation Method

1. Read the revision-bound source files listed above.
2. Count logical TODOs, dispatch attempts, planning rejections, test failures, and publication errors **only** from data present in those files.
3. Record numerator, period, revision, and filter for each count.
4. Mark any unknown historical data as `unknown`.
5. Do not access private Hub state. Do not start productive dispatches.

## Failure Breakdown (from available context only)

| Category | Count | Source | Status |
|---|---|---|---|
| Logical TODOs | unknown | No full journal snapshot provided | unknown |
| Dispatch attempts | unknown | No registry data accessible | unknown |
| Planning rejections | unknown | Not present in revision-bound context | unknown |
| Test failures | 0 observed | No test failure records in provided files | verified (from this snapshot) |
| Publication errors | 0 observed | No publication error records in provided files | verified (from this snapshot) |

**Note:** The zero counts above reflect the absence of such records in the *provided revision-bound context*, not a claim about the full system history. They are **not** a baseline for success-rate measurement.

## Retry-History and Current Attempt

- No retry history is present in the available source files.
- `retries.py` is referenced but its content was not provided in this revision-bound context.
- The current attempt (this task, SR-BASELINE) is a **read-only baseline documentation** task. It does not constitute a dispatch attempt for success-rate measurement purposes.
- No double-counting risk exists because no prior attempts are recorded in the available context.

## The 6/29 Claim

The acceptance criteria reference an "old 6/29 claim" (a historical assertion about baseline metrics). **This claim is explicitly UNVERIFIED** from this snapshot. No data in the provided revision-bound files supports or refutes it. It remains unverified and must not be treated as a confirmed baseline.

## Model / Backend, File Range, File Count, Failure Stage, Test Profile

| Field | Value |
|---|---|
| Model/Backend | Not specified in available context; Hub-issued runtime (ananta) per `analysis_verification.py` schema name `ananta_worker_mutation_report.v1` |
| Date range | Unknown |
| File count (revision-bound) | 6 source files + 1 test file provided in full or partial form |
| Failure stage | Not observable from this snapshot |
| Test profile | `self-development` (per `contracts.py` example and allowlisted pytest command) |

## Six Observed Publication References

The following six publication-related references were observed across the revision-bound context. They are **references to publication mechanics**, not evidence of successful publications:

1. `acceptance_review.py` — defines `AcceptanceReviewRequest`, `AcceptanceReviewReceipt`, and `require_acceptance_review()` which bind a separate reviewer run to a registry entry before publication.
2. `acceptance_review.py` — `verify_acceptance_review()` revalidates stored review authority, checks for self-review, criteria satisfaction, and snapshot mismatch before each publication operation.
3. `coordinator.py` — imports `Publisher` from `agent.services.self_development.publisher` and uses it in `sync_todo_folder()` to publish unblocked/archived items.
4. `coordinator.py` — `import_tasks()` builds a catalog and writes to the journal under lock; publication is gated by policy.
5. `contracts.py` — defines task contract validation including `completion_rule: 'profile_passed'`, which gates when a task may be considered complete (and thus publishable).
6. `analysis_verification.py` — defines `verify_worker_analysis()` which binds observed pre-write source ranges to the exported revision; this is a prerequisite for mutation reports, not a publication event itself.

**Why these do not prove registry validity:** These are code-level references to publication *mechanisms* (classes, functions, imports). They show that the system *has* publication logic. They do **not** demonstrate that any specific run was registered in the Hub evidence registry with valid bindings, correct digests, and independent review. Registry validity requires: (a) a stored `RunEvidenceIdentity` entry, (b) matching input digest, source IDs, result digest, and binding digest, (c) an independent reviewer URL distinct from the author, and (d) all criteria passing. None of these runtime registry entries are present in this snapshot.

## Why Historical Run References Alone Do Not Prove Registry Validity

Historical run references (e.g., task IDs, assignment IDs, dispatch lease IDs mentioned in code or docs) are **identifiers**, not **evidence**. They prove that a run *was attempted* or *referenced*, but they do not prove:

- The run was actually registered in the Hub evidence registry with all required digests.
- The registration passed all integrity checks (input digest match, source ID binding, result digest, environment digest).
- An independent acceptance review was performed and recorded.
- The publication gate was satisfied at the time of publication.

Without access to the private Hub state (the actual registry database), these references are **insufficient** to establish a verified baseline. They can only be used as pointers for future investigation when registry access is available under authorized policy.

## Baseline Verdict

- **Baseline status:** `unverified`
- **Reason:** No full journal snapshot, no registry data, no historical timestamps, and no cohort definitions are present in this revision-bound context. The six observed publication references describe *code mechanics*, not *registered evidence*.
- **Effect on SR-4A:** The requirement for 20 completed baseline dispatch attempts under a pre-documented cohort methodology **cannot be satisfied** from this snapshot. A separate, limited Hub experiment run under already-explicitly-authorized policy must be completed first (per acceptance criteria). This read-only analysis does not start such a run.
- **No post-hoc baseline measurement:** No baseline metrics may be measured after SR-4A implementation. The pre-change baseline is blocked until registry access and cohort data are available.

## Formulas for Future Baseline Calculation

When full data becomes available:

```
success_rate = (completed_tasks / total_dispatch_attempts) * 100
retry_success_rate = (retries_that_succeeded / total_retries) * 100
publication_success_rate = (published_runs_with_valid_registry_binding / total_publication_attempts) * 100
planning_rejection_rate = (planning_rejections / total_planning_requests) * 100
test_failure_rate = (test_failures / total_test_runs) * 100
```

All denominators must be defined by the pre-documented cohort methodology before any measurement is taken.

## Unknowns and Explicit Gaps

| Item | Status |
|---|---|
| Full journal snapshot (state.py data) | unknown — not provided in revision-bound context |
| Registry run history | unknown — private Hub state, not accessible |
| Historical timestamps / cohorts | unknown — absent from this snapshot |
| 6/29 baseline claim | unverified — no supporting data present |
| Dispatch attempt counts | unknown — no registry data |
| Planning rejection counts | unknown — no planner logs provided |
| Test failure history | unknown — only current test suite is verifiable |
| Publication success count | unknown — no registry entries accessible |

## Conclusion

This baseline document confirms that **a controlled pre-change baseline cannot be established from this snapshot**. The revision-bound source context provides code-level evidence of publication and verification *mechanisms* but not runtime *evidence* of registered runs, successful publications, or historical success rates. All quantitative claims remain `unknown` or `unverified`. Before SR-4A can proceed, a separate authorized Hub experiment run must produce at least 20 completed baseline dispatch attempts under a pre-documented cohort methodology.
