# Scrum continuous-improvement operations

The Hub stores immutable loop revisions in `ANANTA_SCRUM_IMPROVEMENT_STATE`
(default `data/scrum-continuous-improvement.sqlite3`). Workers do not initialize
these services. Back up the SQLite file and its WAL consistently; restoring it
restores Sprint, architecture, retrospective, commitment and effect history.

A normal automatic run is:

1. Activate an independently checked architecture baseline.
2. Plan and activate a bounded Sprint against its exact handoff digest.
3. Materialize progress snapshots and invoke adaptive controls on task, gate,
   handoff, budget, scope, architecture or interval events.
4. Apply an authorized backlog adjustment, or use the automated goal-exception
   policy when reachability is classified as `unreachable`.
5. Move through `review`, `retrospective`, `improvement_pending` and `closed`.
6. Build a bounded evidence bundle, preserve multiple perspectives, review low-
   or medium-risk process proposals and bind accepted commitments to Sprint N+1.
7. Compare later loss/cost metrics with the recorded baseline. A regression
   immediately marks the commitment rolled back.

No transition opens an approval prompt or waits for a person. Independent
reviews are explicit service-principal decisions with complete Boolean gates.
Missing evidence, stale revisions, incomplete gates, protected targets, high-
risk process mutations and unavailable architecture revisions fail closed.
Evolution-provider failure is recorded as a bounded analysis status while the
deterministic evidence path continues.

Architecture effect metrics use non-negative loss/cost conventions: lower is
better. Fewer than three observations produce `inconclusive`; a positive delta
produces `regressed`. Do not reinterpret delivery velocity alone as architecture
quality. Use defects, rework, integration failures, latency, reliability loss,
security findings, change cost and debt together where relevant.
