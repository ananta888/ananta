# Research-training threat model

Protected assets are Hub scheduling authority, tenant isolation, budgets, datasets, checkpoints, evaluation
evidence, and release state. Untrusted inputs include API payloads, Worker reports, stage results, artifact
bytes, and evidence identifiers.

Controls:

- closed versioned contracts reject unknown fields, malformed digests, cycles, and capability mismatches;
- the Hub alone creates and releases DAG stages, with optimistic revisions and HMAC attempt fencing;
- Worker code cannot import Hub orchestration, and Hub code cannot import ML runtimes;
- artifact content is size/digest bound, written atomically, tenant-scoped, and executable ingress is denied;
- lineage is immutable and parents must already exist in the same tenant;
- evaluation attestations bind metrics and exact known `SRC_*`/`RUN_*` identifiers;
- automatic release fails closed and can never be bypassed by a human-in-the-loop test or approval.

Residual risks are explicitly accepted only for the non-production mock slice: SQLite is single-Hub storage,
there is no real distributed trainer, quota reservation is not yet durable, retention/garbage collection is
not implemented, and safe executable model export remains unavailable.
