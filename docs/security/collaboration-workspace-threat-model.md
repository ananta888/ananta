# Collaboration workspace threat model

Protected assets are tenant data, room visibility, membership authority, event order, task/tool authority,
artifact references and evidence provenance. Untrusted inputs include browser payloads, live peers, Worker
projections and all external bridge events.

Current controls:

- closed contracts reject unknown fields, oversized payloads, digest tampering and invalid evidence prefixes;
- decision/review/task/workflow/Git projections require both provided `SRC_*` and `RUN_*` identifiers;
- Hub policy evaluates current tenant-scoped membership on every read and write;
- memberships are revisioned, revocation is immediate, cursors cannot regress and presence epochs cannot go stale;
- event append is idempotent and creates an outbox row in the same transaction;
- Workers and external bridge libraries cannot be imported by the collaboration core;
- command events are proposals only and cannot dispatch tasks or tools;
- the disabled bridge fails closed while native Pair-Dev remains available;
- no denial can be bypassed through a human-in-the-loop test or manual approval.

Residual risks blocking release include missing durable inbox processing, per-room restricted-visibility policy,
artifact scanning, secret redaction, retention/erasure workflows, database HA, real load/chaos evidence and a
pinned/conformant Buzz adapter.
