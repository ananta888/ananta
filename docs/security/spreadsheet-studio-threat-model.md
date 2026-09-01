# Spreadsheet Studio threat model

Untrusted assets include workbook archives, formulas, links, embedded objects, macros, model-proposed actions,
Worker results and training projections. Protected assets include tenant documents, immutable versions,
secrets, Hub availability, datasets, adapters and promotion authority.

Implemented controls:

- contracts reject unknown fields, invalid cells, non-finite values, oversized cells/actions and complex ASTs;
- formulas are a closed data AST; URLs, macros, UNO, Python, shell, extensions and free-form formulas are absent;
- hidden-sheet writes, duplicate targets, stale versions and mismatched snapshot digests fail closed;
- the executor returns a content-bound candidate and direct diff but cannot publish it;
- validation and promotion are an atomic Hub transaction with idempotent proposal replay;
- tenant and owner bindings apply to every document API;
- Hub spreadsheet modules cannot import office, document-parser, Worker or ML runtime packages;
- automatic decisions never require or accept a human-in-the-loop bypass.
- production execution is Hub-queued and bound to the central WorkerJob and active slot lease;
- the Worker polls a fixed Hub endpoint and exposes no inbound task API or container port;
- short-lived HMAC capabilities bind artifact reads and callbacks to tenant, job, Worker, lease and assignment digest;
- source artifacts use opaque, tenant-scoped, one-time handles and never appear inline in queue responses;
- exact callbacks are idempotent; stale leases, changed results, capability swaps and handle reuse fail closed;
- the LibreOffice container is non-root, read-only, capability-free, AppArmor/seccomp constrained and attached only
  to an internal no-external-egress control network.

Residual release blockers include broader formula/recalc and workbook-object fidelity, retention/erasure,
durable artifact quotas, multi-Hub artifact storage and grounded production LibreOffice/model-quality evidence.
