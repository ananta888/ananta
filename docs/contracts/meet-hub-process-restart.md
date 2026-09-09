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

The first installed startup exposed a test-control endpoint collision with the
native Hub `health` function. An explicit blueprint namespace fixes it; the
focused reproduction failed in 8.23 s before the fix and 31 control/container
checks passed in 70.71 s afterward. This was a new fixture defect, not a change
to the production health API.

The following installed attempt stayed alive but failed readiness: Docker's
internal network exposed no host binding (`8099/tcp: null`), and the last
bounded access diagnostic was connection refused. Replace the intended host
publication with direct private container addressing. Prepare the stopped,
owned Hub container first, validate its immutable ID/image/name/sole network,
and use its assigned private address. Write the closed Worker origin before
starting that Hub. The Worker fixture may use a non-gateway Hub endpoint only
when it matches this exact owned restart-test container and port. Existing
host-Hub fixtures keep their gateway restriction. No public port, serving
container, broad subnet trust, new network route or timeout relaxation.

## Installed acceptance result

The complete real-clock gate now passes in **168.41 s** with packaged Hub
`sha256:3751c382b380433366aa0506aa436e8d28bf378b8c6052cc7ee08c6e7e53f352`
(source 8faf98a00), separate unchanged Worker
`sha256:55ec55247f2ee83285ac40fb83eb3781b6dbd99e8a15c98447e33a7d3aa745c2`,
and private Meet a98706f (production frontend at 2803903; intervening changes
are fixture/test/planning only).

Actual authorized camera frames completed the native visual child before
SIGKILL of the exact owned Hub. The old runtime/browser stopped in **2833.80 ms**,
inside the unchanged four-second gate, while its Worker container remained.
The private Meet room then contained only its synthetic source participant.
The same Hub container restarted with a different process and the same
persistent database: one dialog Task, unchanged original deadline and already
completed child, no dispatch or new participant. The native background scanner
failed the orphan parent only at its original real-clock deadline, with exactly
one expiry event. Repeated status reads retained that terminal snapshot.

The fixture uses a fixed address from its own private network, checked against
the stopped container's explicit IPAM setting before Worker configuration and
against the running address afterward. No host port is published. The endpoint,
container and existing Worker fixture matrix passed 88 tests in 39.38 s.

Additional fixture defects found and corrected during this gate: the analysis
view unmounts the live participant counter (now read from the actual private
Meet registry), the installed binary also uses `chrome-headless-shell` (now
included in orphan detection), and the UI's active-only Task list excludes a
terminal Task (now counted through an injected scoped all-status SQL query).
The final counter/control checks passed 22 cases in 22.98 s. These failed
attempts are not additional passed restart runs or production defects.
The final JUnit emitted its existing xunit2/record_property compatibility
warning; the closed numeric observation is present in the XML.

All owned containers/networks were cleaned; test-owned SQL remains recoverable
under the pytest temporary directory. This remains synthetic-policy technical
acceptance, not Registry-grounded production evidence, automatic room rejoin,
lossless dialogue resumption or Hub HA. MAP-11 therefore remains open.
