# Spreadsheet Studio operations

The studio is disabled by default. The bounded automatic mock path can be enabled on a Hub with:

```text
ANANTA_SPREADSHEET_STUDIO_ENABLED=true
ANANTA_SPREADSHEET_STUDIO_MODE=mock
ANANTA_SPREADSHEET_STUDIO_AUTOMATIC_PROMOTION_ENABLED=true
```

State defaults to `data/spreadsheet-studio.sqlite3`. The Angular route is `/spreadsheet-studio`; all requests
go through `/api/spreadsheet-studio`. The mock accepts canonical JSON snapshots only. XLSX, ODS, CSV,
LibreOffice rendering/recalculation and real LoRA are intentionally not advertised by mock mode.

Production execution uses `ANANTA_SPREADSHEET_STUDIO_MODE=worker` and the
`compose.spreadsheet-studio.yml` overlay. The Hub persists each immutable assignment, creates the central
WorkerJob and slot lease, and exposes only three internal Worker operations: claim, one-time source-handle
read and result callback. The Worker polls the Hub; it has no listening task port. `ANANTA_SPREADSHEET_WORKER_ID`
must match on both containers and `ANANTA_SPREADSHEET_WORKER_INTERNAL_TOKEN` must contain at least 24
non-whitespace characters. Result callbacks use a separate short-lived per-job capability, never the static
claim credential.

The Worker runs non-root with a read-only root filesystem, all capabilities dropped, no-new-privileges,
Docker's built-in seccomp policy, the default AppArmor profile, bounded CPU/memory/PIDs/file descriptors and
no external network. Its only network is the internal `spreadsheet-control` network needed to poll the Hub.
Exact callback retries are automatic and idempotent; changed replays, expired leases and reused source handles
fail closed.

Production feedback, consent, dataset manifests, split locks, training lineage and revocation impacts use the
same central SQL database as the Hub document control plane. Dataset construction groups document lineage,
instruction templates, formula families and near-duplicates before assigning an immutable split. Consent
revocation atomically records the fencing intent; reconciliation cancels active training automatically and
quarantines terminal lineage without claiming mathematical unlearning. See
`docs/contracts/spreadsheet-learning-v1.md` and `schemas/spreadsheet-studio/dataset-split-lock.v1.json`.

Before live training, persist the execution-backed base report with `POST /api/spreadsheet-studio/baselines`,
then request the Hub decision with
`POST /api/spreadsheet-studio/datasets/<dataset_id>/training-admissions`. A live training request uses
`ananta.spreadsheet-training-command.v2` and supplies the admitted `admission_id`. `no_go` is terminal for that
immutable input combination but keeps automatic base-model-only operation available. See
`docs/contracts/spreadsheet-training-admission-v1.md`.

Run `python scripts/check_spreadsheet_studio_boundaries.py` and
`pytest -q tests/spreadsheet_studio`. The suite uses no network, provider, GPU, office process or person.

## Operations contract and SLOs

The authenticated Hub-admin endpoint `GET /api/spreadsheet-studio/operations` returns the closed
`ananta.spreadsheet-operations-snapshot.v1` projection. Correlation events may contain only bounded opaque
Trace, Task, WorkerJob, Attempt, Document, Candidate, Dataset, TrainingJob and Adapter IDs. They never become
Prometheus labels and raw cell values, formulas, prompts, workbook titles, tenant IDs and artifact bytes are
forbidden. The `/metrics` endpoint exports bounded operation/outcome/reason counters, operation durations,
queue depth and active alerts.

| Operation | p95 objective | Automatic degraded response |
| --- | ---: | --- |
| Queue wait | 30 s | bounded queue/backpressure; Hub APIs remain available |
| Render/recalculation | 90 s | timeout Worker attempt; retain immutable assignment |
| Proposal | 120 s | reject new work when capacity is exhausted |
| Validation | 30 s | reject promotion; preserve candidate and evidence |
| Training | 4 h | base-model-only inference; no blocked online request |
| Result ingress | 10 s | Worker outbox retries the same digest-bound callback |
| Cleanup / retention | 5 min | dry-run/report remains available; defer deletion |

No samples are reported as `not_run`, never as passed. A queue depth of 25 or more activates backpressure;
five or more failed jobs activate the failure alert. During safe shutdown the Hub stops new admissions first,
drains result ingress until its deadline and exits only after durable commits. Jobs remain in the Hub database;
the Worker retains a pending callback in its outbox and retries it idempotently after transport failures.

`POST /api/spreadsheet-studio/operations/reconcile` accepts exactly `max_jobs`,
`artifact_retention_days` and `delete_unreferenced_artifacts`. It automatically terminalizes stale queue rows
with a stable reason while preserving their immutable assignments. Artifact cleanup defaults to dry-run and
deletes only blobs older than the configured retention whose digest is absent from every immutable document
version. Active or referenced artifacts are never deletion candidates.

## Recovery runbooks

All steps below are automatable; no test or normal production run requires a person.

- Corrupt document: stop promotion for the digest, retrieve an earlier immutable version, rerun validation and
  record the corrupt digest. Never repair the stored version in place.
- Worker outage or crashloop: activate bounded backpressure, keep Hub reads available and let stale recovery
  terminalize abandoned attempts. Restarted Workers poll the Hub; workers never create tasks or contact peers.
- Stale lease: reject the expired capability, terminalize the stale queue row automatically and preserve the
  assignment and WorkerJob correlation for a new explicitly scheduled proposal attempt.
- Hub restart: reopen the central SQL queue before admissions, publish queue depth, then allow Worker polling.
  Durable completed results remain replayable with the identical callback digest.
- Outbox replay: retry the exact job ID, capability and payload digest. A changed replay is rejected; a matching
  replay returns the existing terminal projection.
- Storage pressure: enable backpressure, run retention in dry-run mode, then run digest-reference-aware deletion.
  Do not delete database rows or referenced artifacts to free space.
- Adapter quarantine: unload the digest-bound adapter, keep base-model-only inference available and retain its
  evaluation/admission lineage for audit.
- Consent revocation: the Hub records the fencing intent atomically, cancels active training and quarantines
  terminal adapter lineage. It makes no mathematical-unlearning claim.

Production rollout additionally requires broader workbook fidelity, privacy/consent, dataset/training gates and
exact Hub-provided `SRC_*`/`RUN_*` evidence. A mock pass cannot satisfy those gates.
