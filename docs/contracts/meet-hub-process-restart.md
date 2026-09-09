# Private full Hub-process restart gate

MAP-11 already has durable Worker replay tests, actual Worker crash/stall
cleanup and newly constructed deadline coordinators. None is a full Hub
process restart. Add an explicitly opted-in private composition with a packaged
Hub in its own Docker container, a separate unchanged packaged Worker and the
existing private Meet/Chrome fixture. Never restart any serving container.

The Hub container mounts only its test-owned persistent SQLite/data directory
and readonly ephemeral test configuration/keys/CA. Build current tracked code
into an immutable image, using an explicitly supplied immutable test dependency
base; do not mount application sources or reuse a serving database. Reuse the
existing private receive-network/issuer/bridge lifecycle through a small shared
fixture, keeping Hub composition separate from media assertions.

After real authorized visual reception completes, kill only the validated owned
Hub container and start that same container again. Require a different Hub
process, the same persisted Task and original deadline, no redispatch or new
participant, and the old Worker/browser to stop inside existing freshness and
watchdog budgets. The fresh Hub must settle any missing terminal callback only
at the original real-clock deadline and exactly once. An already completed
child must stay completed. Do not synthesize its Worker outcome or extend a
lease to make restart succeed.

The fixture exposes only authenticated, fixed test-control operations inside
its private network. Actual Worker callbacks retain their production signed
route and native task authority. Tests use a synthetic project policy and
source, never production trust or human approval. Only bounded closed counters,
task status and lifecycle observations enter diagnostics.

This is a **fail-closed restart/reconciliation** gate. It does not implement or
claim automatic Meet room rejoin, Hub HA or lossless dialogue resumption. Those
remain separate MAP-11 work. SOLID: the shared fixture owns infrastructure;
the packaged Hub owns orchestration/state; Workers remain execution-only.

## Fixture preparation

The private receive infrastructure is now shared without changing host-Hub
composition or source policy. Eight invalid-source/no-resource tests passed in
12.00 s; the existing real packaged camera/regrant/revoke gate passed in 57.49 s.
The private Hub control/configuration prototype passed 20 headless tests in
19.55 s (closed private origins, key length, explicit opt-in, fixed authenticated
operations, no repeat start or arbitrary execution options). These preparation
checks are **not** the full process-restart result; the installed Hub and actual
restart gate remain to be run.
