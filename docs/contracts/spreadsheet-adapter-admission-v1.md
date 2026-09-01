# Spreadsheet execution evaluation and adapter admission

This slice implements the accepted requirements `SSFR-ML-004` and `SSFR-ML-005`. It uses only their
provided `SRC_0003`, `SRC_0005`, `SRC_0014` and `RUN_0001` evidence identifiers; no source identifier is
created or inferred.

`ananta.spreadsheet-evaluation-report.v2` compares base-model and adapter outputs with the same action schema,
policy, isolated dry-run executor and validator engine used by Spreadsheet Studio. It never persists a
candidate document. The report records strict schema/action/refusal, execution, validator, cell-diff,
unintended-change, latency and resource metrics. Results are grouped by task kind, file format, size, locale,
template cluster, security class and failure class.

Admission is independent of an aggregate text or model score. Every deterministic security and regression
gate must pass, all grouping/diff/resource coverage must be complete, and the report must prove zero
publication, feedback and consent side effects. The report binds the dataset, recipe, split, base model,
adapter, training profile/admission, action schema, serializer, training/evaluation policies, runtime and
evaluation engine through canonical SHA-256 digests.

`ananta.spreadsheet-adapter-admission-command.v1` is a closed admin-to-Hub command. The Hub derives existing
registry promotion evidence rather than accepting caller overrides for known digests. It requires the
registry's previously verified source/run provenance, uses optimistic concurrency and idempotency, records
the complete digest set in immutable promotion history, and atomically transitions only an evaluated adapter
to `approved`.

Inference remains action-only and approved-adapter-only. The optional repair strategy can remove exactly one
JSON Markdown fence; it cannot change actions, policy, scope or capability and audits only output digests.
Inference proposals are never auto-applied. Consent revocation automatically fences active training,
deprecates affected approved adapters, rolls back to a prior approved adapter or the base model, and requests
runtime cache unload. Offline runtime cleanup is reported as retry-pending, never as a human gate.
