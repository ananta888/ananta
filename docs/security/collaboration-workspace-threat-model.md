# Collaboration workspace threat model

Protected assets are tenant data, room visibility, membership authority, event order, task/tool authority,
artifact references and evidence provenance. Untrusted inputs include browser payloads, live peers, Worker
projections and all external bridge events.

Current controls:

- closed contracts reject unknown fields, oversized payloads, digest tampering and invalid evidence prefixes;
- decision/review/task/workflow/Git projections require an immutable binding verified by the Hub Evidence Registry;
- Hub policy evaluates current tenant-scoped membership on every read and write;
- memberships are revisioned, revocation is immediate, cursors cannot regress and presence epochs cannot go stale;
- event append is idempotent, quota-bound and creates an outbox row in the same transaction;
- restricted rooms are filtered in room lists, timelines, threads, search, memory, cursor and presence paths;
- task, workflow, Git, review and artifact flow projections repeat the restricted-room check for the requesting actor;
- outbox delivery uses attempt fencing, expiring leases, bounded backoff and terminal failure states;
- external inbox replay binds origin, adapter, external ID, mapping version and payload digest;
- command approval is digest- and policy-revision-bound and always terminates automatically as approved or blocked;
- resource use requires a Hub assignment, verified offer, exact task binding, expiry, budget and fencing token;
- artifact events contain only bounded, scanned digest references; secrets and private reasoning are rejected;
- prompt sections carry explicit trust classes; external and retrieval instructions remain untrusted data;
- bridge signing keys are resolved through secret references, can be rotated/revoked, and every use extends a scoped hash-chain audit;
- multidimensional budget consumption is atomic, while cancel/revocation traffic cannot be starved by an abuse budget;
- Workers and external bridge libraries cannot be imported by the collaboration core;
- command events are proposals only and cannot dispatch tasks or tools;
- the disabled bridge fails closed while native Pair-Dev remains available;
- no denial can be bypassed through a human-in-the-loop test or manual approval.

Residual risks do not block the local Native Core lane. They do block broader
claims: multi-Hub requires a shared persistence/CAS adapter and split-brain
evidence; SFU/TURN and n>2 production claims require real multi-browser runtime,
load, soak and revocation evidence; Buzz production release requires a real
pinned-relay run and registry-verified evidence. Local deterministic tests and
synthetic signatures never satisfy those external production gates.
