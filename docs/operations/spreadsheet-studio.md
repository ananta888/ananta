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

Run `python scripts/check_spreadsheet_studio_boundaries.py` and
`pytest -q tests/spreadsheet_studio`. The suite uses no network, provider, GPU, office process or person.

Production rollout additionally requires recovery/retention, broader workbook fidelity, privacy/consent,
dataset/training gates and exact Hub-provided `SRC_*`/`RUN_*` evidence. A mock pass cannot satisfy those gates.
