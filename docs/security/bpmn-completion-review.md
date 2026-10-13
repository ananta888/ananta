# BPMN completion security and recovery review

Scope: local synthetic tests and isolated Hub/Worker/browser acceptance on
2026-10-11. These are technical observations, not registered production release
evidence. No deployment or BPMN production flag is enabled.

## Verified integration

- Signed per-invocation control leases bind tenant, workflow, run, plan, policy
  and control task. They have finite expiry, CAS acquisition and monotonic fences.
  Process-exit takeover is tested independently of exception cleanup.
- Queue admission now uses the actual `TaskQueueService.ingest_task_fenced`
  and `TaskRepository.insert_native_task_fenced`. Lease validation, exact duplicate
  comparison and Task insertion share one authority-database transaction.
  The new path retains existing status policy and post-commit behavior; legacy
  unfenced ingestion is not a BPMN fallback.
- SQLite event/checkpoint/wait and SQLAlchemy event/checkpoint/Task recipients
  validate the locked current signed lease, not a separate pre-write read.
  SQLAlchemy lease revisions and recipient writes share an immutable anchor lock.
  Result acknowledgement and failed-attempt metering use fenced ownership
  recipients as well. Expiry/takeover injected inside these recipients denies
  stale mutations. The in-memory adapters are test-only.
- Exact persisted Task receipts recover admission after a queue/event/checkpoint
  interruption, with node/input/attempt/fence/plan/policy/control/correlation
  validation and current grant revalidation. No missing receipt is invented.
  A crash before admission may end in a bounded persisted failure.
- Cancellation also adopts admitted tasks missing from the Native checkpoint,
  then cancels/revokes them without dispatch. Already revoked receipts can be
  read for cancellation recovery only, never to reauthorize execution.
- Acknowledgement binds canonical result bytes, not just a caller result ID.
  Result scope, tool/artifact containment and finite nonnegative usage are checked
  before durable acceptance. Failed work consumes budget; exhausted runs persist
  failure before acknowledging or dispatching successors.
- Supplied stale checkpoints cannot substitute for the authoritative checkpoint.
  XML definitions, region projections, plan hashes, message targets and explicit
  command revisions remain bound. Graph recompilation is stable; delegated
  audit correlation comes from the Hub command, not a Worker-generated identity.
- Native catch-up accepts only known gateway observations, never an intervening
  graph control transition. Gateway audit-only appends have their own bounded
  sequence-conflict retry; exact replay retains the first event and changed
  dedupe content is denied.
- Synthetic Evidence Registry tests actually admit immutable sources and reserve
  test runs before simulated execution. Unknown, mutated, stale or synthetic
  identity claims cannot satisfy a production gate.

## Acceptance and limitations

The 22-case isolated container suite includes production queue, assignment,
Worker authentication, results, signed SQL state, explicit approvals/denials,
XOR/default, AND, bounded loop, embedded subprocess, timer/message catches,
SQL recomposition and public graph start/replay. Chromium uses the actual
Angular editor/API service and real production login route against that Hub.
A small fixture login form and synthetic release-admission port remain explicit.
No container shares the Hub database or signing key with a Worker.

The separate unit/browser-double suites cover adversarial boundaries and
editor races more broadly. Tests do not use skips/xfails to hide an unsafe
transition. Numerical results and exact scope are recorded in the active TODO.

## Still open — production admission remains blocked

1. **Production artifact rollout.** The Native ingress/receipt contract and its
   positive container transfer are implemented and tested with synthetic bytes.
   This does not grant production runtime release admission or cover real
   container-loss recovery. Positive Full-App Native Autopilot dispatch is
   covered by the separate gate below. Recovery ingress remains
   a separate authority contract.
2. **Operational/packaging evidence.** Full application boot is covered by the
   separate `--full-app` bootstrap gate below; Native dispatch and artifact
   handoff are covered by `--full-app-autopilot`. Its durable outbox,
   container/database loss, PostgreSQL concurrency,
   production image packaging and locked-dependency frontend build are not
   established by this suite. No minipc test or production release was performed.
   CaseFlow trace composition and richer event/region property forms remain
   narrower UI integration work.

## Local ingress and mutation-recipient hardening on 2026-10-10

Native Worker responses are now checked before generic forwarding projections
and artifact normalization. A separate Native completion policy validates the
immutable command, exact result/envelope projections, attempt/fence, terminal
status, declared artifacts and compiled component output inside the existing
Task save/CAS transaction. That transaction also locks current ownership,
assignment, registered Worker and authorization grant and verifies the Hub
signature. The Hub records a content-bound `native_result_receipt` with
`technical_observation` classification. Native polling requires this receipt;
legacy unreceipted Worker fields cannot become a canonical success. Exact replay
retains the original receipt after authority expiry; changed content, scope,
command, receipt or terminal status fails closed. Pi keeps its stricter provider
result policy through a compatibility adapter over the shared authority reader.

Ownership claims, grant issuance/revocation, Task cancellation and Hub-side
side-effect planning/authorization now have optional fenced recipient ports.
SQLite and SQLAlchemy recipients validate the signed control lease inside the
transaction that writes the mutation. PostgreSQL recipients lock the immutable
checkpoint anchor before their domain rows. Missing fenced ports block BPMN;
there is no unfenced fallback. Task cancellation retains status CAS, history and
normal post-commit notifications. Failed/stale cancellation cannot publish those
notifications. In-memory fenced adapters remain synthetic test implementations.

The completion/recovery harness now composes SQLAlchemy checkpoints, ownership,
events, grants and the side-effect ledger on one authority database. The queue
remains explicitly synthetic. New tests inject expiry inside recipients and
exercise superseded leases, foreign scopes, malformed/contradictory results,
receipt tampering, exact replay and real Task save/CAS rollback.

The expanded local regression run passed 893 tests with one adapter-specific
skip. Its deselected embedded-subprocess loop test passed separately. A further
17 cancellation and side-effect recipient tests passed, including a new check
that even terminal-task cancellation no-ops reject a foreign workflow scope.

Worker side-effect execution still uses its existing assignment/attempt and
signed authorization boundaries; a short-lived Hub control-invocation lease is
not granted to a Worker. These changes do not provide a distributed transaction
or external exactly-once execution. Positive artifact ingress and the operational
items above remain open. Docker access is denied in the current sandbox, so the
updated hostile-result container cases have not been rerun here. Local pytest
commands are technical observations, not Hub-reserved release evidence.

The shared `ananta_contracts.native_artifact_transfer` contract now validates
actual bounded bytes against independently supplied Task, command, assignment,
attempt, fence and dispatch-lease bindings. Closed fields exclude Worker
database identities, filesystem paths and claimed Hub receipts. It rejects
undeclared/duplicate/missing outputs, noncanonical base64, size/hash mismatch
and transfer limits before any persistence side effect. Its 29 local tests
passed. This is transport validation only: authenticated ingress, immutable
Hub storage/registration and transactional receipt admission remain to be
connected. The BPMN artifact preflight denial remains in force.

`NativeArtifactAdmissionService` now reads and locks the authoritative Task,
requires current SQL Native ownership/assignment/grant authority and delegates
dispatch admission through a narrow transaction-bound port. It validates the
whole transfer before storage, uses the existing immutable ArtifactStore, and
registers Artifact/Version rows in the caller transaction without committing.
Content-bound Hub artifact IDs support retries after rollback/orphaned bytes;
these IDs are not `SRC_*` or `RUN_*` evidence. Conflicting registration and
tampered stored bytes fail closed. Forty local artifact tests passed, including
the ten new real SQL/storage cases. Their dispatch port is explicitly synthetic:
the production dispatch adapter, authenticated endpoint/Worker transport and
result-receipt integration remain open. SRP separates transfer validation from
materialization; DIP/ISP keep dispatch and immutable storage behind small ports.

Native completion now resolves every artifact reference against locked Hub
Artifact/Version registrations in the actual Task transaction. Exact command,
assignment revision, attempt/fence, tenant/project and declared output bindings
must match; worker-local references, absent/rolled-back rows and modified
registrations cannot complete the Task. The technical result receipt records
the admitted artifact/version IDs and binding digest. Existing Pi admission
remains separate. The production dispatch adapter and ingress/Worker transport
are still required before enabling artifact-bearing BPMN plans; the new result
check does not independently assert continued dispatch-lease validity. Shared
row locking now lives in the repository infrastructure seam, avoiding a service
dependency on another service's private SQL helper.

The subsequent `NativeArtifactDispatch` adapter now uses the production Native
authority reader rather than a synthetic dispatch port. Native dispatch is
already fenced by the active execution ownership lease and Hub Worker assignment;
it has no Recovery-child dispatch token. The adapter revalidates current SQL
ownership/assignment, worker registration/capabilities and signed grant under the
caller transaction, then matches authenticated worker ID/URL. A stable digest
binds the exact command, assignment revision, attempt/fence and Worker; mutable
heartbeat/expiry timestamps are not identities. Completion independently checks
that same dispatch-binding digest against its current validated authority.
The 88 local artifact/result/Pi tests passed with real SQL dispatch validation
and synthetic task inputs. Strict HTTP authentication, endpoint composition and
Worker transfer integration remain open; artifact-bearing BPMN start is still
denied until those paths are implemented and verified.

The authenticated Hub HTTP path is now registered at
`/internal/tasks/<task_id>/native-artifacts`: GET supplies only the currently
assigned Worker's transfer binding/declared outputs; POST validates bounded
actual bytes and commits Hub Artifact/Version registration before returning
technical receipts. Its narrow `task.native.artifacts.publish` scope requires
strict registered-worker auth and Native capability, with no shared Hub-token
fallback. Production composition requires the Hub role. Rate and body limits,
audit events, caller transaction ownership and bounded signature/lease denials
are implemented. Seventeen HTTP tests exercise actual registry-keyring auth,
SQL authority and immutable storage; the expanded artifact/Native/Pi suite
passed 133 tests before the additional Hub-role regression passed separately.
Worker publication/receipt consumption and positive BPMN execution still remain
to be connected; the start denial remains. SOLID: the route owns HTTP handling,
the runtime owns transaction composition, and the admission service owns
materialization. The existing broad tasks blueprint registry remains unchanged
in responsibility; one additive blueprint is registered there.

`NativeArtifactPublisher` now implements Worker publication through a narrow
transport port. Before POST it verifies the Hub binding against its exact
delegated command/task/attempt/fence and declared outputs, then validates bounded
bytes. The shared receipt validator accepts only closed, exact byte/command/
assignment/tenant/project bindings and content-derived Hub artifact/version IDs;
it rejects worker-local references and claimed evidence identities. Fifty-seven
HTTP/transfer tests passed, including publication through real Flask registered
auth, SQL/immutable storage and actual Task completion, plus hostile binding and
receipt mutations. The injected transport in these tests uses Flask's test
client, not a network socket. Production HTTP transport, Worker output-byte
resolution and execution-path composition remain open; BPMN artifact start is
still denied. SRP separates publication from HTTP transport and local byte
resolution; the Worker neither admits artifacts nor creates Hub identities.

`NativeArtifactHttpTransport` now provides the production GET/POST transport.
It requires file-managed Worker credentials/identity, restricts request targets
to the Native artifact path, bounds request/response size and timeout, and
reuses the exact-target no-redirect HTTP helper. Eleven local transport tests
passed, including real JSON/header serialization into the Flask auth/SQL/storage
endpoint and Task completion through an intercepted HTTP opening seam. Four
real localhost network cases were skipped because creating TCP sockets returns
`PermissionError` in this sandbox; they remain unverified and must run in a
socket-enabled environment. This is a separate limitation from unavailable
Docker access. Worker output-byte resolution and execution-path composition
remain open before BPMN artifact execution can be enabled.

`LocalNativeArtifactReader` now resolves Worker-local references through
injected repository readers and a confined artifact root. Exact task/tenant/
project/command/attempt/fence/output metadata, current local version size and
hash are required. Descriptor-relative no-follow opens reject traversal,
file/directory symlinks, multiple hard links and nonregular/FIFO files without
waiting for a writer. `NativeNodeArtifactPublication` connects this reader to
the publisher. The default Native node handler now requires publication before
executing artifact-producing tasks and returns Hub-admitted references only.
Publication failure after execution raises to the delegated runtime so its
existing claimed-side-effect uncertainty path is retained. Forty-two reader/
Native adapter/runtime tests passed, followed by the reader suite including the
new hard-link denial. Production root injection and producer metadata binding
still need composition; the BPMN start denial remains. SRP separates byte
reading, publication and command execution; injected repository callbacks avoid
new Worker imports of Hub business services. Existing broader Native handler
and composition-module responsibilities are preserved architectural debt.

Worker startup now composes the file-managed HTTP transport, publisher and local
byte reader and passes the publication port through workflow-adapter composition
to the Native node handler. LocalArtifactRows provides only read-only SQL
artifact/version lookups using injected models/engine; it imports no Hub task
repositories or orchestration services. Startup/Pi readiness tests passed 28
cases; the subsequent startup/composition run including publication injection
passed 14. Inspection of the ordinary command executor shows its current
`artifact_refs` are trace-description lists rather than named stored outputs.
Producer-side task-bound output-file materialization therefore remains a real
open requirement; runtime wiring alone does not enable positive artifact
execution. The configured Worker still fails closed on missing materialized
outputs. The existing startup function remains a broad composition root; new
business logic stays in Worker reader/publication modules.

The ordinary Worker executor now supports explicit
`node.metadata.native_output_files` declarations, keyed exactly by declared
output artifact names. Each descriptor contains only a relative workspace
`path` and `media_type`. NativeOutputFileMaterializer validates the complete
declaration before command execution, then snapshots bounded confined files
after a completed command. LocalNativeArtifactWriter stores immutable local
bytes and task/command/attempt-bound Artifact/Version rows without invoking Hub
ingestion/extraction services. The executor replaces its trace-description
list with the actual named local references consumed by the publication path.
Startup now injects this materializer alongside publication. Missing or unsafe
post-execution files raise NativeOutputMaterializationError through the handler
to preserve the delegated runtime's uncertainty handling. Fifty-two output,
reader, startup and adapter/composition tests passed; command execution in the
new producer tests uses a deterministic stub writing real files, not a full
command-policy or release run. Positive compiled BPMN execution across this
whole producer/transfer/result path and downstream artifact consumption remain
open before removing preflight denial. SOLID: output declaration/snapshot,
local persistence, byte resolution and Hub publication stay in separate
collaborators; existing broad composition/executor modules only inject and call
those seams.

NativeArtifactInputService now admits downstream bytes only for a current
consumer assignment and a named input explicitly authorized by its signed
command. The Hub resolves the exact bound reference itself, requires a completed
producer with an immutable result receipt in the same tenant/project/workflow/
run/plan/policy/control-task scope, and revalidates its Hub Artifact/Version
registrations before loading bounded immutable bytes. No arbitrary artifact-ID
read API is exposed by this service. The immutable store now opens reads
nonblocking and checks regular-file/link-count/size before reading, so FIFO
replacement terminates without waiting for a writer. Fifteen input/store tests
passed using real SQL producer/consumer authority and real stored bytes,
including wrong Worker/project, changed run projection, missing receipt, expiry,
tampered bytes and FIFO denial. Input HTTP/Worker download/workspace consumption
and complete compiled-BPMN producer-to-consumer execution remain open.
SRP keeps input authorization separate from output admission and generic byte
storage; the existing broad ArtifactStore remains shared infrastructure.

The strict `task.native.artifacts.read_input` HTTP scope and GET endpoint at
`/internal/tasks/<task_id>/native-artifact-inputs/<input_name>` are now registered.
The runtime owns the SQL transaction and returns exact producer provenance with
the consumer command digest; callers choose a declared input name, not an
arbitrary artifact/version ID. NativeArtifactInputDownloader verifies the
closed response, exact consumer/task/reference bindings, tenant/project, source
provenance-derived immutable IDs and bounded bytes/hash before returning data.
The HTTP transport permits only GET for this input path and uses a separate
single-artifact response limit; upload receipt limits remain small. Sixty-one
input/HTTP/transport tests passed, with four real TCP cases skipped for the
documented sandbox socket denial. Tests include actual Flask registry auth,
SQL producer/consumer authority and stored bytes with hostile download edits.
Workspace materialization and pre-execution Worker composition, plus full
compiled-BPMN artifact acceptance, remain open. HTTP authorization, transaction
composition and Worker byte validation stay in separate SRP collaborators.

NativeInputWorkspaceMaterializer now downloads and validates all declared
inputs before placing files at exact `node.metadata.native_input_files` paths.
It rejects undeclared/duplicate/conflicting paths and input/output collisions.
Descriptor-relative no-follow directory traversal, staged writes and atomic
no-overwrite links protect existing targets; idempotent placement accepts only
exact matching regular files with a single link. Symlink, hardlink and FIFO
targets fail without blocking or overwriting. Worker startup injects this port
into the executor, which materializes inputs before invoking the command and
fails closed if the port is missing. Sixty-four input/workspace/output/startup/
adapter tests passed, including actual Hub auth/SQL/download/proof validation
followed by a synthetic command reading the installed bytes. Fifteen security
architecture/documentation regressions also passed. Full compiled BPMN graph
execution, real command-policy/container acceptance and TCP tests remain open;
the artifact start denial remains. SRP separates workspace placement from Hub
download/validation and execution; existing executor/composition breadth remains
preserved debt.

### Source-bound artifact file declarations

The compiler now preserves `native_input_files` and `native_output_files` in
the canonical node metadata and binds them, together with task IO, to the
imported XML for ordinary graphs as well as region projections. Request loading
rejects IO or file-declaration changes outside the source. Export/reimport
preserves the declarations. A pure shared contract validates relative paths,
closed descriptors, media types and colliding input/output targets in the Hub
and Worker; Worker filesystem confinement remains a separate responsibility.
The 103 source-binding, workspace, output, routing and XML-region checks passed.
The subsequent full BPMN regression run passed 806 tests, skipped four TCP
cases because the sandbox cannot create sockets and deselected one slow-loop
case under the existing suite policy.
These are synthetic local technical observations, not production release
evidence. The compiled BPMN artifact start gate remains until complete runtime
composition and command-policy acceptance are verified.

### Actual command-policy composition

The artifact executor now uses the existing schema-supported `task_context`
profile source. The previous `workflow_adapter` value caused the real Worker
request validator to reject execution. The closed Worker result schema accepts
the existing optional `provider_hash` provenance field, constrained to a
registry digest or the explicit `native` sentinel for commands without an
external model. This field is transport metadata, not Hub evidence identity.
Previously the real runtime rejected its own successful result after executing
the command. Existing consumers may continue omitting this optional field.

Actual local subprocess tests now cover output creation through the production
Worker command planner, policy classifier, tool registry, resource enforcement,
schema verification and immutable local artifact writer. A second test covers
real authenticated Hub input retrieval, immutable proof validation and confined
placement followed by a production-policy command reading and checking the
exact bytes. A command requiring approval returns a bounded blocked result,
without execution or artifact rows. Missing output materialization now rejects
before command execution. These tests use synthetic assignments and remain
technical observations; they do not prove complete compiled-graph execution,
container boundaries, TCP operation or production release readiness.

Fresh Hub input-service composition now reopens the persisted SQL database and
immutable storage instead of reusing the original service objects. Recovery
accepts the exact completed producer receipt after its execution lease expires,
while an expired current consumer or mutated persisted producer receipt is
denied. The input suite passed 27 tests, including these three recovery cases.
This is a component reconstruction test, not a process/container crash or a
complete compiled BPMN recovery claim.

SRP/DIP remain protected by the existing injected runtime and materialization
ports. The broad production Worker runtime still combines planning, schema
verification and execution-result shaping; this is preserved SRP debt rather
than a new orchestration path.

### Declared input readiness before delegation

The Hub delegation service now rejects a task with a missing or empty declared
input reference before provider selection, execution ownership, authorization
grant issuance or queue submission. Previously projection silently omitted
missing inputs and relied on a later Worker download failure. The real Native
orchestrator regression proves a bounded failed run without queue submission or
Worker execution; all 16 readiness/Native runtime checks passed. Hub byte access
continues to validate registered immutable provenance independently. This
precondition stays in Hub delegation (SRP); no Worker orchestration is added.
Compiled BPMN artifact start admission remains gated pending complete acceptance.

### Compiled graph and persistent Task composition foundation

A new integration regression compiles real BPMN XML, uses the production
Native queue adapter and fenced SQL Task-ingestion recipient on the shared Hub
authority database, binds synthetic registered Worker dispatch assignments,
executes actual policy-checked subprocess commands through the Native delegated
runtime and stores results through the production Task completion policy. The
Hub is reconstructed after each task result. The run completes with exactly two
persisted tasks, one execution per node and Hub-owned technical result receipts.
The targeted integration test passed. This foundation currently contains no
artifact IO; complete compiled artifact handoff is still pending. Worker
dispatch is synthetic, execution is in-process and no container/TCP/release
evidence is claimed. Injected queue/runtime ports preserve Hub orchestration
and separate Worker execution; the existing broad composition debt remains.

### Current compiled artifact admission status

The compiled integration now also covers artifact IO. A real policy-checked
producer writes its declared output in its task workspace. Separate Worker SQL
and immutable storage hold the local snapshot. The production publisher/reader
ports transfer bytes through the production Hub artifact runtime, SQL authority,
registration and storage. Task completion admits the exact Hub artifact receipt.
After Hub reconstruction, the consumer receives the persisted Hub reference,
downloads and validates it through the production input service/downloader,
installs it in its own workspace and runs a real command checking the exact
bytes. The complete compiled graph reaches `completed`, with exactly one
execution per task and a registered Hub artifact reference in the checkpoint.

Accordingly, the former unconditional compiled BPMN artifact denial is replaced
by a declaration gate: all task IO must have exact, safe `native_input_files` /
`native_output_files` declarations under the shared closed contract. Missing or
invalid declarations still return `bpmn_artifact_ingress_unavailable`; existing
legacy plans keep their compatibility behavior. Current lease/assignment,
immutable Hub admission and result-receipt checks remain mandatory during
execution. These declarations do not grant byte access or release evidence.

The full BPMN regression passed 816 tests, skipped four TCP cases because the
sandbox cannot create sockets and deselected one slow-loop case under the
existing suite policy. Fifteen security/documentation regressions also passed.

This paragraph supersedes earlier progress notes saying the compiled artifact
start gate remains unconditional. Dispatch identity and the artifact transport
in this integration are synthetic in-process seams. Separate HTTP tests cover
registered Worker authentication; real TCP and container acceptance remain
unverified in the sandbox. No production release claim is made. SRP keeps the
declaration contract separate from transport, byte storage and Task admission;
Hub ownership of task orchestration remains unchanged.

## SOLID check

Protected: SRP through separate XML compiler, input projection, event waits,
lease validation, receipt recovery, Task ingestion and runtime-provenance modules;
DIP/ISP through small optional fenced/read ports on existing boundaries.
LSP/OCP: legacy APIs remain additive and BPMN fails closed without required
recipients. No orchestration moves to Workers.

The new result projection, transactional authority reader, completion policy,
forwarding guard and cancellation service have separate responsibilities. The
existing Task completion-policy port and optional guarded CAS port keep domain
validation out of repository persistence code (DIP/ISP). Pi authority names
remain compatibility exports with unchanged additional capability requirements.

Preserved debt: the large Native orchestrator, gateway and Angular component
still coordinate multiple concerns. TaskRuntimeService retains broad status and
post-commit coordination; cancellation reuses its notifications rather than
duplicating them. Neutral WorkflowRequest validation invokes
the separate BPMN compatibility facade; TaskQueueService still has transitive
route-ordering imports. Further decomposition should use bounded domain ports,
not a replacement runtime or an independent queue.

## Container artifact acceptance 2026-10-11

The full unselected isolated Hub/Worker/Chromium gate passed 22/22 cases in
67.00 seconds, with complete cleanup. Producer bytes are admitted through the
production authenticated Native ingress and SQL authority, committed as Hub
artifacts/technical receipts, then downloaded and verified by the consumer.
An unadmitted replacement reference is rejected before Task completion, receipt
persistence and ownership acknowledgement. Host artifact/runner regression:
57 tests passed in 10.14 seconds. The Hub signing key is ephemeral, private and
loaded through the production file-managed configuration; only public verification
material reaches the Worker. This supersedes earlier notes describing positive
container transfer as missing. Full-App, Autopilot, real container-loss recovery
and production packaging/admission remain open.

Test composition keeps byte storage, HTTP transport, authority and orchestration
separate (SRP/DIP). The fixture's shared dependency image and worker-local imports
of Hub schema/storage are retained packaging debt; separate containers and data
remain mandatory, and this is not a standalone Worker packaging acceptance.

The final negative case preserves consistent repeated envelope facts and uses
the actual Worker-local artifact reference. It must fail specifically with
`native_artifact_hub_receipt_required`; envelope inconsistency alone is not
counted as an artifact-registration test. The final full 22-case rerun passed
in 67.00 seconds with cleanup. Additional 92 file/transfer/input/Todo tests
passed in 8.42 seconds (149 targeted tests across both suites).

## Full application bootstrap and Legacy composition follow-up

`python3 scripts/test_bpmn_container.py --full-app` now starts the real
`create_app(testing=False)` in its own fresh, isolated container. It verifies the
Hub Memory runtime, required routes, health and real login, plus registered-Worker
and public workflow authentication with no unauthorized Task writes. Required
read-only packages/prompts/GeoMap/SFU/LiveKit configuration are mounted explicitly;
startup validation remains enabled. Background schedulers are explicitly off.

The gate exposed two actual composition defects: Hub Memory used legacy
`AGENT_ROLE` instead of the authoritative bootstrap `ROLE`, and Native artifact
routes lacked the `/api` paths used by the Worker transport. The role resolver now
uses `ROLE` whenever present and accepts `AGENT_ROLE` only when absent. Additive
Native `/api` aliases share the existing authentication/rate-limit/authority views;
unprefixed endpoints remain compatible. The fixture uses this same registration.

Full bootstrap passed with complete cleanup. A separate selected producer/consumer
handoff and unadmitted-reference denial rerun passed 2/2 cases with cleanup.
These are synthetic technical observations. Positive full-app Autopilot dispatch,
durable outbox/container-loss recovery and production release admission remain
open; the new bootstrap gate does not claim those capabilities.


## Full application Native Autopilot gate — 2026-10-11

The bounded `--full-app-autopilot` profile passed in 35.26 seconds with complete
cleanup. The actual Hub application dispatches through authenticated Autopilot
HTTP ticks to a separate Worker. A single Native node and a two-task artifact
handoff complete with Hub-admitted technical result receipts. No generic LLM
proposal or local Hub execution handles those Native tasks.

The new execution projection uses Ed25519 over a closed Task payload, exact
Worker identity/URL and short expiry. Worker-side verification precedes the
existing live Hub assignment/lease/grant checks. The Hub selects only strictly
registered Workers with authorized Native capability. Invalid/unknown outcomes
remain blocked without invented terminal receipts or blind redispatch.

989 regression tests passed; the separate core container/Chromium matrix passed
22/22. A further 66 focused checks passed after isolating dispatch dependencies
per application. These are synthetic technical observations: graph admission
and deterministic Worker execution are fixtures, not production release inputs.
The [curated gate](../../artifacts/test-gates/bpmn-full-app-native-autopilot.v1.json)
records the exact scope. Durable outbox/container-loss recovery, PostgreSQL,
actual provider/tool execution and production packaging remain open.

The dispatch module separates transport, assignment and result admission through
small injected ports (SRP/DIP). Existing broad Autopilot tick orchestration,
legacy import reexports and global SQL/signing-key composition remain preserved
SRP/DIP debt; they should migrate incrementally to focused application services.


## Durable dispatch journal increment — 2026-10-11

The Hub now persists a unique tenant/Task send reservation before Native HTTP.
An unknown outcome survives engine/process reconstruction and cannot authorize
a second send, even after a status reset. Receipt reconciliation checks the
actual SQL Task admission, command digest, Worker identity/URL and assignment
revision. A caller's fabricated receipt and foreign assignment are denied.
A bounded tenant-scoped headless batch returns accepted/unresolved results
without dispatching or changing Task state.

994 broad regression tests and 40 final focused checks pass (overlapping suites).
The complete Full-App Native/artifact gate passes in 35.38 seconds with cleanup
and explicitly asserts accepted journal rows. Channel inventory now contains
500 components; the new table is Hub control state and stores no credentials
or executable payloads. SRP separates persistence from bounded reconciliation;
DIP injects the journal into the dispatch port bundle.

This is the durable send boundary, not complete delivery recovery. Actual
mid-execution container loss, Worker-result redelivery, scheduler integration,
pre-send recovery and authorized new attempts remain open. The
[journal gate](../../artifacts/test-gates/bpmn-native-dispatch-journal.v1.json)
contains synthetic technical observations only.

## Actual Worker container loss containment — 2026-10-11

The new isolated `--full-app-worker-loss` mode terminates the Worker after
production Native consumer execution and before its result response. Docker
exit-state inspection independently proves process/container loss. The actual
Hub stays responsive, retains an unknown durable journal outcome, and blocks
another send after an explicit fixture-driven status reset through the normal
repository. Two further authenticated ticks cannot create a completion receipt;
tenant-scoped headless reconciliation reports unresolved and the workflow does
not claim completion. No mandatory security gate is disabled.

The host runner starts only this fresh Compose project's services detached,
follows Hub logs, requires the expected Worker exit and successful Hub exit,
and cleans containers/networks. No Docker socket or production configuration
enters the containers. A separate normal Full-App Native/artifact run proves
that fault injection is opt-in. Runner doubles check wrong exits, absent
identities/reports and timeout cleanup; they do not substitute for Docker loss.

SRP keeps the loss scenario in its own test module and host lifecycle in the
runner. Existing monolithic application boot/global runtime composition remain
preserved architectural debt. This closes the missing real-loss containment
observation; Worker-result redelivery and eventual recovery completion remain
open. All observations use synthetic graph admission and execution handlers.

Verified final loss gate: 28.98 seconds with full cleanup; normal Full-App
Native/artifact regression: 35.42 seconds. The 35 focused tests passed in
5.02 seconds. See [the stable loss gate](../../artifacts/test-gates/bpmn-worker-container-loss.v1.json).


## Native Worker result recovery implementation — 2026-10-11

The productive Worker execution port now requires its private configured
SQLite result store, reserves before execution and commits output before HTTP
response. A separate recipient-bound signed result endpoint retrieves without
executing. The Hub's bounded recovery service admits only original
journal-bound transport-review Tasks with current lease/grant/assignment and
strict registered Worker authority. Unrelated review and Pi's stricter policy
remain separate. Journal settlement now matches the Task-before-journal lock
order to avoid introducing a PostgreSQL admission/reconciliation deadlock.

1042 broad regression tests and 69 final focused checks pass (overlapping).
The normal full application/artifact publication container gate passes in
36.49 seconds; real Worker loss containment passes in 30.01 seconds. Both
clean up completely. Worker SQLite reopening, signed HTTP retrieval and actual
Hub SQL admission are component-tested with synthetic fixtures. Complete
container recreation with persistent-volume redelivery is still open, as are
scheduler wiring, retention maintenance, tool/filesystem isolation, PostgreSQL
and productive packaging/admission. POSIX-private storage and logical expiry
do not prove physical erasure or isolation from arbitrary same-UID tools.

SRP separates Worker persistence/configuration, Hub retrieval transport and
journal-bound admission; explicit ports preserve DIP. Existing global engine/
key composition remains debt. See
[the recovery gate](../../artifacts/test-gates/bpmn-native-worker-result-recovery.v1.json).


## Full application Worker recreation/redelivery — 2026-10-11

The complete gate passes in 34.19 seconds. Worker-local output survives actual
process exit and Docker container replacement in an ordinary Worker-owned
persistent volume. The full Hub uses production recovery, retrieval, forwarding
and SQL admission to complete Task/journal/workflow under the original current
assignment. Original result hostname and replacement Worker hostname differ;
the original send claim remains unchanged. Normal Full-App Native/artifact
regression passes separately in 35.52 seconds. 45 focused checks pass in
5.92 seconds; cleanup includes volume absence checks.

The initial tmpfs volume failed this requirement and was replaced with an
ordinary local-driver data volume. The finite networkless permission initializer
has CHOWN only and mounts no Hub/source state. Worker execution stays
unprivileged. All content/admission/handler/status-reset fixtures are synthetic.
The [curated gate](../../artifacts/test-gates/bpmn-worker-recreation-redelivery.v1.json)
proves this exact loss-after-publication/current-lease recovery scope.
Scheduler wiring, lease expiry, new attempts, retention/tool isolation,
PostgreSQL, productive packaging/providers and release admission remain open.


## Hub scheduler reconciliation verified — 2026-10-11

The opt-in extension retrieves one scope-selected Native result per existing
Hub tick. Worker-role, stopped, disabled and manual-review paths perform no
port construction; explicit disable overrides environment opt-in. Tenant/Task
SQL filtering and Goal/Team snapshot recheck prevent selecting a different
pending Task. Persisted tick count rotates candidates without another loop.

The full application Worker recreation/redelivery gate now completes through
an authenticated Autopilot HTTP tick, with `recovered=1` and `dispatched=0`.
It passed in 37.92 seconds with full container/network/volume cleanup.
1037 broad regression tests and 34 focused checks pass (overlapping suites).
SRP separates scheduler policy/selection from retrieval and admission; existing
legacy tick imports/global application composition remain preserved debt.
Synthetic graph admission and deterministic handlers remain explicit; this is
not release evidence. See
[the scheduler gate](../../artifacts/test-gates/bpmn-native-result-scheduler.v1.json).


## Expired dispatch / exact Hub retry policy (2026-10-11)

Full application container gate passed in 40.45 seconds: real Worker recreation,
injected SQL execution-lease expiry, old result denied, signed cancellation from
transport review, unapproved retry denied and exact Hub-policy retry completed
with a fresh Task/attempt and higher fencing token. Old dispatch remains unknown.
Normal current-lease redelivery regression passed in 38.95 seconds; both gates
verified container/network/volume cleanup. Broad regression: 996 tests passed
in 66.45 seconds. Synthetic graph/handler, injected expiry and programmatic
signed controls remain explicit limits; no production release evidence.
See `artifacts/test-gates/bpmn-expired-dispatch-retry.v1.json`.
Next: retention maintenance and tool filesystem isolation, PostgreSQL,
production packaging/providers/release admission. BPMNX-007/014/015 stay partial.


## Worker result retention follow-up (2026-10-11)

Bounded startup/request-time expiry and a finite headless maintenance module
are implemented. Expired payloads/digests are scrubbed without content reads;
live results, replay tombstones and the store quota remain preserved.
Broad regression: 1009 passed in 68.41 seconds. Final focused check: 31 passed
in 4.99 seconds, with one additional test beyond the broad run. Isolated
networkless UID-65534 cached-image storage check: 23 passed in 1.13 seconds.
Full-app Worker recreation/redelivery passed in 38.81 seconds with cleanup.
The first minimal container mount set could not import the existing shared
Hub contracts; the successful check explicitly includes read-only agent code.
Standalone Native packaging debt remains; no production release claim.
Next: OS tool isolation, Hub/deployment-controlled idle maintenance cadence,
authority-bound tombstone retirement, PostgreSQL and production admission.
BPMNX-007/014/015 stay partial. Report:
`artifacts/test-gates/bpmn-worker-result-retention.v1.json`.


## Shell/private-store isolation follow-up (2026-10-11)

Mandatory Landlock (ABI >=6 plus erratum 3 fix) and metadata Seccomp now
protect the configured private result store at the shared Worker Shell port.
Pinned workspace FD, clean environment, closed unrelated FDs and no fallback.
Actual Full-App Hub/Worker dispatch/admission gate passed in 35.47 seconds:
workspace writes allowed, same-UID SQLite read/write/truncate/chmod denied;
native and artifact-handoff workflows completed. All project resources cleaned.
Broad regression: 1145 passed in 70.19 seconds; final focused: 56 passed
in 3.59 seconds, including the additional erratum/config-drift and 22 inventory tests.
Static synthetic graph/handler/commands remain explicit; no production release
evidence. Other provider/tool ports, ARM execution, network/resource profiles,
idle maintenance/tombstone retirement, PostgreSQL and release admission remain
open. BPMNX-007/014/015 and ZAM-007 stay partial. Report:
`artifacts/test-gates/bpmn-private-store-tool-isolation.v1.json`.

The active cached store remains authoritative after config removal. Ordinary
sandboxed Shell jobs cannot perform owner-store maintenance; idle housekeeping
needs an explicitly authorized owner/administration execution context.

### PostgreSQL Native dispatch/result concurrency (2026-10-12)

`scripts/test_bpmn_postgres.py --image sha256:<full-image-digest>` starts
only an owned temporary PostgreSQL container. The tested cached image is
`sha256:5a65324fe84dc41709ff914e90b07f3e2f577073ed27bf917d4873aca0c9ec51`.
The container runs UID999 with readonly root, no capabilities, bounded CPU,
memory/PIDs, tmpfs database storage and a localhost-only ephemeral host port.
Every case owns a schema; schemas, container and network are removed afterwards.
The serving database is never queried or modified by this gate.

Six actual PostgreSQL cases pass in 16.75 seconds:

- Two independent SQL engines obtain one durable dispatch claim.
- Concurrent identical terminal writes preserve the exact admitted receipt.
- Contradictory terminal writes cannot replace the first accepted result.
- Current authority-row locks block another connection's lease mutation with
  PostgreSQL `55P03`; after the lock is released, a superseding lease blocks
  stale result admission.
- Unknown transport settlement cannot downgrade concurrent accepted journal state.
- Caller rollback removes the send fence and Task transition together.

Task mutation tests use separate Hub lock-port instances with real PostgreSQL
advisory locks. They do not share a local Python mutex. Actual Task repository,
authority, completion-policy and journal code are exercised; production code
was not changed for these tests. Statement/lock timeouts, finite barriers and
a 90-second pytest limit prevent interactive or indefinite waits. The harness
requires JUnit to show six executed tests with no skips, failures or errors.
Without explicit owned fixture configuration, direct pytest execution skips
these six cases instead of selecting a serving database.

SRP/ISP/DIP: Container lifecycle and test assertions are separate; existing
repository and lock ports remain the execution seams. Existing global engine
and lock-locator coupling remains debt; test ContextVars add no productive
singleton or Worker orchestration.

This proves Native dispatch/result database boundaries, not the complete
PostgreSQL BPMN reference matrix or independent full Hub processes. Compiled
wait/control concurrency, browser/reference flows, idle maintenance cadence,
authority-bound tombstone retirement and production packaging/provider/release
acceptance remain open. Technical synthetic observation only; no RUN/SRC or
production release claim is invented. Report:
`artifacts/test-gates/bpmn-postgres-native-concurrency.v1.json`.

### PostgreSQL event-wait components and delegated result expiry (2026-10-12)

The owned PostgreSQL gate now requires nine executed cases with no skips.
The three additional component tests race timer cancellation/consumption,
duplicate message delivery/consumption and stale control-lease mutation through
independent SQL engines and the actual checkpoint compare-and-swap store.
The successful run passed nine cases in 22.04 seconds. The first extended run
exhausted the disposable database's 256MiB tmpfs; fixture-only storage and WAL
limits now bound the database to 512MiB with 32/64MiB min/max WAL settings.
These tests bind a compiled XOR plan but use synthetic catch targets and a
synthetic message authorizer. Full compiled timer/message workflows and
independent Hub process acceptance remain open. Report:
`artifacts/test-gates/bpmn-postgres-event-waits.v1.json`.

`native_result_maintenance` is now an explicit delegated Native operation.
The Worker profile defaults it off. Enabling it requires the task kind and
`workflow.result_maintenance` capability; the Hub still owns authorization,
task ingestion and dispatch. The only allowed tool is `native.result.expire`.
Inputs are limited to a batch size of 1..1000. The operation erases expired
payloads for the actual signed Task tenant/project and keeps replay tombstones.
It cannot choose a path, schedule another task or issue evidence identities.

An additive SQLite scope table binds new result slots to their original project
and immutable task digest. Existing slots without a provable scope are not
guessed or backfilled. Scoped maintenance suppresses global startup/request
expiry, including result retrieval; ordinary owner maintenance retains its
existing behavior. Actual signed Worker HTTP tests verify that other tenants
and projects keep their expired payloads during the scoped operation and that
repeat execution cannot run the operation again.

The productive Hub queue composition derives maintenance scope from an actual
active control Task and Project before ingestion. Missing/foreign/terminal
control Tasks and archived/missing Projects fail before creating a child.
Duplicate commands must also retain the identical persisted project scope.
The shared scope validator now serves Pi and maintenance through separate
composition adapters. This protects SRP/DIP and preserves Pi's public error
contracts. Existing repository/global engine locator coupling remains debt;
an explicit repository/engine provider would improve that composition seam.

The focused scope/maintenance/Pi compatibility run passes 37 cases. This proves
SQL-backed control/project validation and in-process delegated execution,
including signed Worker HTTP and the actual SQLite store. Automatic Hub cadence,
exact Worker-store destination binding, full productive container acceptance
and authority-bound tombstone retirement remain open. No production deployment
or grounded release evidence is claimed.

The broad BPMN/Pi/queue-adapter/security/startup regression passes 1661 cases
in 116.30 seconds, with eleven explicit skips (nine owned PostgreSQL tests
executed separately, two real-model checks), one deselection and four existing
startup Pydantic deprecation warnings. Ruff, memory-channel inventory,
legacy-memory access, CLI shim imports, Todo consistency and diff checks pass.
Report: `artifacts/test-gates/bpmn-delegated-result-maintenance.v1.json`.

The final registration follow-up also suppresses the maintenance capability
when opt-in is absent or false. The 36-case handler/Pi-composition/startup check
passes in 10.03 seconds; its four warnings are the same startup deprecations.
This prevents a disabled handler from being advertised as an available
maintenance destination. It does not claim automatic destination selection.

### Exact registered Worker/private-store binding

Bound maintenance inputs add a closed `result_store_owner` object containing
`worker_id`, canonical `worker_url` and `store_binding_id`. The ID is a deployment
domain reference, not an Evidence Registry identity or a filesystem path.
Legacy explicitly delegated batch-only inputs remain compatible; automatic Hub
cadence must always use the bound form.

A dedicated Worker opts in through its typed Native profile:

```json
{
  "enabled": true,
  "allowed_task_types": ["native_result_maintenance"],
  "capabilities": ["workflow.result_maintenance"],
  "result_maintenance": true,
  "result_store_binding_id": "private-results-v1"
}
```

This extends the existing profile; the runtime still requires the existing
executor and authorization-verifier composition. Only a successfully built
adapter advertises the binding as
`execution_limits.native_result_store_binding_id`. Hub registration validates
and preserves it. The productive queue preparer reloads the exact registered
Worker, requires strict registration provenance, current online status,
authorized Native/maintenance capabilities, matching name/URL/store binding and
bounded liveness. It inherits the real active control Task/project and pins the
child's `assigned_agent_url` before ingestion. A repeated command cannot accept
a changed registered store or persisted scope.

Autopilot routing never substitutes another Worker for a bound owner. Dispatch
reloads registration immediately before signing. Both the signer and independent
Worker signature consumer require the declared recipient. The Worker checks its
installed registration binding before creating or opening the private store,
and the maintenance handler independently checks the bound store before calling
its expiry port. A cached store cannot be relabeled when registration changes.
Mismatch produces a bounded failure, with no alternate-store execution.

The binding identifies the explicitly configured application-owned store. A
deployment must use a new binding when replacing that store; this feature does
not discover private filesystem paths or prove physical erasure. It preserves
scoped expiry, replay tombstones and default-off policy. Automatic cadence,
authority-bound tombstone retirement and full productive container acceptance
remain separate open requirements.

SRP/ISP: closed input parsing, read-only registry eligibility and Worker store
composition remain separate. Existing global repository/engine lookups and the
large Autopilot dispatcher remain preserved DIP/SRP debt; a later explicit
registry-provider adapter can replace the composition seam without widening the
owner-validation port. Report:
`artifacts/test-gates/bpmn-result-store-owner-binding.v1.json`.

### Durable automatic Hub cadence for private-result expiry

`AGENT_CONFIG.native_result_maintenance` adds an explicit, default-off Hub
preauthorization. Its closed schedule binds tenant/project, an existing active
Hub control Task, the registered Worker/private-store owner, interval and batch.
Example for the existing administrator-managed Hub configuration:

```json
{
  "native_result_maintenance": {
    "enabled": true,
    "auto_approval": true,
    "policy_revision": "native-maintenance-policy-v1",
    "schedules": [{
      "schedule_id": "private-results-project-a",
      "tenant_id": "tenant-a",
      "project_id": "project-a",
      "control_task_id": "existing-active-hub-control-task",
      "owner": {
        "worker_id": "registered-worker",
        "worker_url": "http://registered-worker:5000",
        "store_binding_id": "private-results-v1"
      },
      "interval_seconds": 600,
      "batch_size": 100
    }]
  }
}
```

The existing lifecycle-owned Hub workflow reconciler performs one finite tick.
No Worker timer or independent orchestration loop is introduced. The interval
range is 600..86400 seconds, with at most 64 configured schedules and 64 pending
reservations globally. Each finite, low-priority plan delegates exactly one
`native.result.expire` batch of 1..1000. A pending run is polled within at most
five seconds even if the general reconciler interval is configured slower.

`native_maintenance_cadence` persists the policy manifest/digest, ordinal,
next due time and exact current Run before grants or queue effects. Independent
SQLite connections serialize reservation with a write transaction; PostgreSQL
uses a capacity advisory lock, conflict-safe initial insertion and row locking.
Restart and uncertain outcomes reuse the same reservation instead of creating
a replacement. Missed intervals do not create a catch-up burst.

Automatic approval is insufficient by itself. The Hub revalidates the actual
active control Task/project and strict live registered owner; production also
uses the existing protected runtime selector, observed health, budget/locality,
project/tenant rollout and current immutable release admission. Only live Native
selection is accepted, with no runtime or Worker fallback. A mandatory
`approval_lifecycle.human_required_tools` rule for `native.result.expire` blocks
the headless attempt. It returns a bounded machine-readable blocked result and
never prompts or bypasses that policy. The capability catalog declares support
for delegated maintenance/Pi orchestration; it grants no Worker permission.

Generic Native plans can opt into the existing signed Hub control lease through
`hub_control_lease_required=true`; this does not fabricate a BPMN definition.
These plans persist an initial signed checkpoint before their first delegation,
plus a bound Hub deadline. The maintenance run deadline is 120 seconds; silence
causes durable failure and fenced cancellation of queued work. The node budget
is 60 seconds with one attempt. Actual crash-after-queue tests recover the same
durable child rather than reexecuting Worker work.

Withdrawal, malformed preauthorization, policy revision/owner change or failed
admission cancels pending work before a replacement can be reserved. An already
confirmed completed run retains its outcome. Before-start cancellation retires
the reservation under the same current Run lease and same-transaction lease
validation; the delegation policy independently requires that exact reservation
to remain current. An older Hub invocation cannot resurrect a retired intent.
Unknown composition/transport/persistence outcomes remain pending and blocked;
they are never reported as completion or replaced speculatively.

All IDs in this journal are ordinary domain references. The report is technical
observation with synthetic policy fixtures, not production release evidence.
Local acceptance covers actual SQL queue/grants/assignment, independent Ed25519
Worker HTTP verification, actual scoped private SQLite expiry and Hub terminal
receipt admission. Independent PostgreSQL checks cover exact concurrent cadence
reservation and generic Native control leases, alongside the previous nine
Native/event-wait cases. Full compiled reference/container/PostgreSQL acceptance
and authority-bound Worker replay-tombstone retirement remain open.

SRP/DIP/ISP: closed policy/plan construction, SQL cadence journal, finite domain
tick, read-only admission and runtime composition use separate small seams.
Existing global repository/engine composition and the larger Native scheduler
remain preserved coupling/SRP debt. The fast existing Hub thread remains the
single lifecycle owner of periodic orchestration. Report:
`artifacts/test-gates/bpmn-native-maintenance-cadence.v1.json`.

### Replay-preserving result compaction foundation (2026-10-12)

The private Worker result store can now replace an expired, exactly scoped slot
with a smaller permanent denial marker. The marker retains tenant/Task identity,
the original private-result binding digest and a digest of the Hub permit. It
does not retain result content. Reservation checks markers before result quota;
reopening, changed project/input bindings and concurrent requests cannot reopen
an old execution. Result capacity and marker capacity are separately bounded.
Marker exhaustion rejects the entire batch transaction without deleting slots.
This releases logical result capacity, not guaranteed physical disk space or
cryptographic erasure. No marker deletion or unrestricted store rotation exists.

Compaction accepts only a closed Ed25519 permit bound to the maintenance Task,
immutable command, tenant/project and registered Worker/store owner. Its lifetime
is at most 60 seconds and cannot exceed the current command grant. Entries are
unique, content-free and bounded by the delegated batch. The Worker verifies
the signature and scope before I/O and again after acquiring SQLite's write
lock, so waiting does not extend a permit. Nonexpired, unscoped, differently
bound and foreign-project slots are preserved.

The Hub historical admission adapter verifies the actual terminal Native/Pi
receipt, accepted dispatch journal, immutable dispatch projection and exact
recipient. The registered owner must authorize the stronger retirement
capability. The exact old grant and execution lease must have expired; missing,
unknown, altered or contradictory facts deny admission. Grant projection checks
remain owned by the authorization service, and ownership checks reuse its exact
SQL/JSON decoder. Private-result hashing is shared without changing its existing
ASCII JSON format or evidence-assignment bindings.

This foundation is **not yet a productive retirement workflow**. The existing
expiry-only handler explicitly rejects `retire_expired=true`, and registration
does not advertise retirement. Remaining implementation: current maintenance
authority and policy checks during Hub permit issuance, a bounded candidate scan,
explicit Worker composition/revalidation, a durable Hub acknowledgement journal
that advances beyond already compacted candidates, and cadence reconciliation
under restart, revocation and unknown outcomes. The BPMN tasks remain partial.
The subsequent maintenance-capacity implementation below resolves the full-store
reservation dependency. Superseded historical ownership is conservatively denied
by the current admission adapter and still needs exact history-based proof.

SRP/ISP: shared binding, permit verification, local SQLite persistence and Hub
historical authorization have separate responsibilities. The existing grant
service owns both current and expired exact grant validation. Existing global
runtime/Flask composition and broad Native scheduling remain preserved DIP/SRP
debt; no Worker gains queue ownership or orchestration. All local tests are
synthetic technical observations, not production release evidence.

### Bounded maintenance capacity and durable private owner (2026-10-12)

Configured owner-bound private stores now provide up to 64 additional maintenance
slots when ordinary result capacity is exhausted. Existing maintenance first
uses ordinary capacity, preserving its previous capacity behavior. The reserve
is globally bounded per store, not per tenant. Only a closed maintenance Task
bound to the installed store identity and a nonempty project can use it; generic,
unbound and foreign-store Tasks cannot. Its durable classification rows carry
the exact private binding and are counted under the same SQLite write transaction
as reservation. Independent connections cannot overrun the reserve. Zero disables
extra capacity; nonintegers and values outside 0..1000 are rejected.

Expiry preserves both the result replay fence and emergency-capacity reservation.
Only signed Hub compaction removes a capacity reservation, atomically with moving
the result to a permanent denial marker. This allows subsequent maintenance to
reserve a slot while previous Tasks remain unreplayable. Unknown outcomes keep
their bounded capacity; they are not silently freed or treated as accepted.

The installed private-store identity is now persisted in SQLite. First explicit
registration binds a legacy/unbound private store additively. Reopening with the
same identifier succeeds; another identifier is refused before expiry or result
changes. Concurrent first bindings admit one identity. Opening without an explicit
identity preserves compatibility but grants no extra maintenance capacity. The
application resolver passes its actual installed registration identity; caller
inputs cannot choose a database path or rebind an existing store.

The productive maintenance HTTP execute path now reads current Hub permission
before resolving or reserving a store. Malformed, revoked, unavailable or nonboolean
decisions deny allocation. A focused `NativeExecutionAdmission` port shares the
existing Hub authorization adapter and command projection with the consumer. It
does not execute, consume a nonce, create tasks or orchestrate work. The consumer
retains its `consume`/`supports` public interface and still performs its normal
execution-time authorization. Result retrieval retains its existing contract.

An actual SQL Hub queue/grant/assignment, independently signed Worker HTTP and
Hub terminal receipt test now runs with all ordinary result capacity occupied.
Another actual SQL grant-revocation test proves that a still-valid transport
signature cannot consume the reserve or scrub payloads after revocation. Other
tests cover independent connections, restart, full/disabled reserve, signed
compaction/replay preservation and durable identity races. These remain synthetic
technical observations; productive retirement issuance/acknowledgement composition
and the full BPMN reference/container/PostgreSQL matrix remain open.

SRP/ISP/DIP: capacity accounting remains in the private persistence adapter;
read-only admission has its own narrow port, and command-to-Hub binding is shared
without broadening the consumer interface. Existing Flask/global runtime fallback
composition remains coupling debt. Report:
`artifacts/test-gates/bpmn-maintenance-capacity.v1.json`.

### Hub-issued retirement and durable acknowledgement (2026-10-12)

Retirement is now wired into the actual Native Autopilot branch. An explicitly
enabled cadence schedule sets `retire_expired=true`; its closed plan requires
both maintenance and retirement capabilities/tools. The Worker profile must
also explicitly set `result_retirement=true`, enable maintenance, declare the
retirement capability and bind a private store. Missing opt-in does not advertise
retirement or fall through to a generic executor. Existing schedules omit the
new false flag so their serialized policy and plan hashes remain unchanged.

The Hub issuer revalidates the current schedule, active control Task/project,
mandatory human-tool rules, full runtime/release/rollout admission, current
cadence reservation, exact Worker assignment, grant and lease. Candidate scans
and signing run in the same transaction as persisting the manifest into its Task.
A signed closed Task cannot leave before commit. A manifest cannot be added
after a dispatch reservation exists; an already issued manifest is reused only
with the exact stored Task and still-valid permit. Final signing rechecks policy,
authority and time after the bounded scan.

The Hub journal separates issued manifests from confirmed private slot bindings.
A tenant/project/owner/store cursor selects terminal accepted dispatches before
applying its limit; it advances across temporarily ineligible records and wraps
without excluding unknown slots as if they had been retired. Confirmed bindings
are excluded from subsequent scans. Retirement is capped at 128 entries with
200-byte Task IDs to fit the repeated bounded Native result envelopes; expiry
still supports batches of up to 1000. There is no payload/history admission into
the signed manifest. SQLite uses a write transaction; PostgreSQL uses scoped
advisory/row locks and bounded statement/lock timeouts while preserving stricter
existing settings.

The opted-in Worker validates the exact signed manifest before scrubbing or
compacting. It revalidates current Hub authority before effects, after expiry and
after compaction. Existing exact denial markers are acknowledged idempotently;
absent, unexpired or differently bound slots are not claimed as retired. It
returns the manifest digest, actual compaction count and exact retired bindings
in its normal Native result. Replay markers remain permanent, including when a
new permit reconciles compaction performed under an earlier interrupted permit.

Only an actual completed Task with an admitted Native receipt and accepted,
unchanged dispatch projection can confirm the Hub journal. Confirmation verifies
the issued signature, exact assignment/recipient and output membership/digest;
parallel/idempotent confirmation cannot widen an existing marker. The existing
Hub maintenance tick reconciles accepted outstanding acknowledgements after
restart even when new execution is disabled. It processes bounded batches with
a finite time budget. Invalid acknowledgements become observable blocked rows;
database unavailability is not invented as success and does not disable ordinary
expiry cadence. No new Worker loop or Worker-to-Worker route exists.

Historical admission now also handles a proven superseding lease. The current
canonical ownership must have a higher fence under the same tenant/workflow/run/
step, and the exact immutable Hub history revision named by the accepted receipt
must match the original attempt/fence/owner. Its original grant must be expired.
Missing or mutated history is denied; a caller cannot manufacture a history
identity or declare a lease inactive. This is an ordinary Hub domain projection,
not issuance or promotion of Evidence Registry `SRC_*`/`RUN_*` identities.

Local acceptance includes actual SQL queue/grants/assignment, actual Autopilot
dispatch, independent Ed25519 Worker HTTP, private SQLite compaction, actual Hub
receipt acceptance, idempotent confirmation, interruption before acknowledgement,
revocation, policy withdrawal, wrong assignment, post-dispatch issuance rejection
and exact/missing/mutated history. Owned PostgreSQL adds independent-Hub cursor
concurrency and rollback/timeout preservation to the existing eleven cases.
These remain synthetic technical observations. Full compiled BPMN reference,
productive container/provider/registration/release and complete PostgreSQL
acceptance are still open; the BPMN tasks remain partial.

SRP/ISP: the SQL journal owns persistence/scanning, the issuer owns one existing
Task's authorization/signing/receipt lifecycle, and the dispatch integration uses
a narrow prepare/confirm port. Exact historical ownership decoding remains in
its owning infrastructure module. Existing large Native scheduling/ownership
modules and global Flask/engine composition remain preserved SRP/DIP debt.
Report: `artifacts/test-gates/bpmn-result-retirement-pipeline.v1.json`.

### Complete Native reference matrix in the full application (2026-10-12)

The full-application Autopilot container gate now covers all thirteen Native
reference scenarios: service, artifact handoff, XOR true/false/default, bounded
loop, subprocess, approved/rejected/blocked UserTask, timer, message catch and
AND join. It boots the actual application with `testing=False`, performs real
login, drives authenticated Autopilot HTTP ticks and uses the actual persistent
Hub queue/assignment/grants/result admission plus a separate deterministic Worker.
Each run verifies accepted dispatch journals, technical Native receipts and actual
Task/edge/event/output observations. The AND case observes a pending sibling and
proves successor time and event order after both sibling completions.

The final owned two-container run passed in 96.42 seconds and removed its exact
containers/network/volume. The current core/browser gate also passed all 22 cases
in 68.71 seconds, including real authenticated Chromium editor operation, SQL
recomposition and denied cross-scope/revision/evidence boundaries. These are
distinct acceptance scopes; no subset is represented as complete coverage of
optional backends or production release admission. Twenty runner safety tests
passed in 2.83 seconds. Report:
`artifacts/test-gates/bpmn-full-app-reference-matrix.v1.json`.

SRP: explicit policy command construction and the bounded asynchronous tick/
sibling observation use small dedicated test modules. Existing full-app scenario
verification/driver breadth is preserved test-harness coupling debt. No application
execution policy is replaced to manufacture positive outcomes. Remaining work
includes full PostgreSQL reference/fault coverage, pre-reserved synthetic Hub
Evidence Registry runs for the complete acceptance chain, and optional/productive
runtime admission. BPMN tasks stay partial pending those scoped requirements.

### Pre-reserved Registry identities for full-app Worker reference Tasks (2026-10-12)

All sixteen Worker Tasks in the thirteen full-application reference cases now
receive real Hub Registry reservations before execution. This uses actual SQL
control Tasks/projects, current strict Worker assignment/ownership/grant proof,
synthetic immutable task-input source admission and caller-owned Registry
transactions. The Registry alone issues source/run identities. The exact closed
projection is persisted before the actual Autopilot signs/reserves its dispatch,
and the separate Worker validates/returns that projection without minting or
promoting identities. Generic dispatch gains only an optional preparation seam;
missing preparation preserves previous behavior, and errors or rewritten native
command/tenant/recipient bindings prevent dispatch reservation and transport.

The Hub accepts the ordinary Native result under its exact current authority,
then completes/verifies the registered run with the actual result digest and
closed assignment/lease. It requires reservation time at or before Worker start
and verifies that each synthetic test run fails a production release gate.
Actual HEAD and a bounded selected-source digest identify the dirty worktree;
the owned runner rejects source changes during execution. No repository `.env`,
production credentials, Docker socket or shared Hub signing/database mount is
introduced into the Worker.

The final full-application two-container gate passed all thirteen references and
sixteen unique test RUNs in 112.34 seconds, with owned cleanup verified. Broad
regression passed 1795 cases (24 explicit skips, one deselection, four existing
startup warnings) in 129.74 seconds; 80 focused Registry/dispatch/runner cases
passed in 12.27 seconds, plus final 33 runner/dispatch cases in 4.93 seconds.
The blocked/rejected UserTask controller observations produce no Worker RUN and
remain technical observations, not falsely completed Worker evidence.

SRP/OCP: isolated source identity calculation, fixture Registry lifecycle and
pre-dispatch preparation use separate seams. The existing broad Autopilot and
runner orchestration remain preserved SRP/coupling debt; image validation is
extracted rather than weakening its complexity gate. Full PostgreSQL/fault
coverage, controller/event/browser Registry identities and optional/productive
runtime admission remain open. Report:
`artifacts/test-gates/bpmn-full-app-registry-runs.v1.json`.


### Full application on owned PostgreSQL (2026-10-12)

The reference, Worker recreation/redelivery and expired-lease exact-policy retry
gates now run against an owned PostgreSQL Hub database. All thirteen references
and sixteen pre-reserved test RUNs passed in 55.42 seconds. Actual recreation
and redelivery passed in 43.96 seconds; expiry/retry passed in 43.69 seconds.
Each gate verified complete cleanup. Twenty-five runner/database isolation
checks passed in 3.57 seconds. Workers retain private SQLite; no shared database,
host port, serving configuration or production credential is introduced.

The initial PostgreSQL gate failed before bootstrap because the container
isolation guard only admitted SQLite. The guard now requires an explicit Hub
opt-in and the exact owned PostgreSQL fixture URL. It rejects Worker use,
foreign hosts, altered URLs and execution outside Docker. SQLite's immediate
transaction syntax is used only for that dialect; PostgreSQL retains normal
caller-owned transactions. Bounded failure frames exclude exception messages,
source text and locals.

SRP: option validation, database lifecycle and transport admission use focused
functions. Existing broad full-app orchestration and global engine composition
remain preserved SRP/coupling debt. Controller/event/browser Registry coverage,
complete UI recovery/commands and optional/productive release acceptance remain
open. Fault-mode observations do not claim Registry reservation coverage.
Report: `artifacts/test-gates/bpmn-postgres-full-app.v1.json`.


### Registry reservations for actual Hub control operations (2026-10-12)

The Native orchestrator now accepts a narrow optional control-lease port;
production defaults still use the original signed lease service. The full-app
fixture decorates that actual service to reserve a real Registry test RUN before
Hub control execution. Assignment and dispatch bind the actual lease owner and
signed lease checkpoint. Sources bind the original plan/inputs/parallel limits
and the full signed approval/message command, including actor and payload.
Completion requires unchanged SQL Task tenant/project and the actual persisted,
verified signed workflow checkpoint. Registry failure rolls back identities and
prevents the action. Changed inputs/commands/scopes leave the original RUN
uncompleted. No Worker identity issuance or policy bypass is introduced.

The final PostgreSQL gate passed thirteen references with 132 controller RUNs
and sixteen Worker RUNs in 51.75 seconds. The final SQLite gate also passed all
thirteen references with actual pre-reserved control/Worker identities; its exact
count/time is in the stable gate report. Both verified complete owned cleanup.
Blocked/rejected workflows correctly have control RUNs and zero Worker RUNs.
Every test RUN fails production release verification; controller success denotes
an executed control action, not a completed workflow. Broad regression passed
1145 cases (13 explicit skips, one deselection) in 91.16 seconds, followed by
263 command/lease/SQL-Registry/event/runner checks in 14.21 seconds.

SRP/OCP/DIP: a narrow injectable lease port enables composition around the
existing authority, while Registry lifecycle remains a dedicated collaborator.
The existing broad orchestrator composition and test driver retain SRP/coupling
debt. Fault-mode/browser Registry reservations, full UI recovery/commands and
CaseFlow tracing, and optional/productive admission remain open.
Report: `artifacts/test-gates/bpmn-hub-control-registry.v1.json`.


### Registry lifecycle through actual Worker failure and recovery (2026-10-12)

The full-app fault modes now reserve real Hub Registry identities for every
Worker attempt and signed Hub control invocation before execution. The original
lost-response Worker RUN remains reserved/unverified until its exact current
assignment/result is admitted. Successful redelivery completes the same RUN;
lease expiry leaves the old RUN uncompleted, and exact Hub retry preauthorization
creates a distinct pre-reserved RUN for the higher-fenced fresh attempt. A denied
control action also stays reserved, without an invented terminal result.

All four owned gates passed: PostgreSQL redelivery40.04s/retry40.51s, SQLite
redelivery40.39s/retry42.28s. Each verifies actual Worker recreation and complete
container/network/volume cleanup. Fifty-two focused cases passed9.77s; broader
BPMN/Task/runtime/runner regression passed1863 cases with13 explicit PostgreSQL
skips and58 deselections in186.15s. No serving deployment or production evidence.

The fault scenario found a productive state-machine gap: Native preparation
could correctly refuse execution but fail to persist review-required from `todo`.
The canonical `request_review` table now supports pre-execution states; the
existing assigned/proposing exception is unified there. The transition removes
dispatch eligibility and cannot reopen terminal/reserved Tasks. Actual SQL and
container assertions prove persistence without force or false execution status.
The fixture also stops treating control ownership as generic parent dependency.

SRP/OCP: review semantics have one transition table; synthetic control provisioning
and pending Registry observations have focused collaborators. Existing broad
fixture orchestration remains preserved coupling debt. Browser Registry and full
UI/CaseFlow recovery/command acceptance remain open, along with optional and
productive runtime admission. Report:
`artifacts/test-gates/bpmn-registry-fault-recovery.v1.json`.


### Authorized source recovery after actual browser reload (2026-10-12)

A new owner-only BPMN definition read exposes a closed projection of the original
persisted source, runtime identity and hashes. Raw-byte SHA and canonical plan/
policy binding reject mutated XML even when a caller also updates its source
SHA. Public workflow aliases are distinct from the admitted BPMN definition;
that distinction is preserved. Existing Hub read authorization applies without
requiring a live backend query or starting work. Other owners/tenants get no XML.

The editor's explicit recovery verifies types, byte quota and WebCrypto digest
before import, preserves concurrent draft edits, and verifies that current Hub
run/plan/definition still match. Recovery grants no start readiness. Actual
Chromium page reload and reauthentication restore the original edited source and
completed state with one start and two Worker Tasks. All22container/browser
cases passed67.55s with owned cleanup;1192backend cases passed93.11s (13explicit
PG skips,1deselection),41focused passed5.56s and67frontend passed3.27s. The changed
TypeScript dependency closure passed with existing repository ambient types.
Full-app TypeScript is blocked by missing cached ECharts/Game-Core dependencies.

An existing route test lacked ROLEhub and therefore could not satisfy mandatory
Hub retry policy context. Its fixture now declares the actual Hub role; no retry
policy is removed. SRP: immutable snapshot projection is a focused collaborator,
while the existing broad authorized backend/editor retain coupling debt. Browser
Registry, complete UI command recovery, expanded CaseFlow traces and optional/
productive runtime admission remain open. Report:
`artifacts/test-gates/bpmn-definition-browser-recovery.v1.json`.


### Source-bound expanded CaseFlow edge lineage (2026-10-13)

CaseFlow now derives expanded edge lineage from the admitted immutable BPMN XML
and canonical plan/policy binding. Original element, activation, direction,
lexical scope, iteration and activation count are closed source facts, never
Worker/event assertions. Catalog IDs and directions must match the regenerated
source exactly; replacing a source with another otherwise valid predecessor is
rejected. No source lineage is promoted to executed/release evidence: missing
canonical event correlation still produces an unverified trace.

The UI retains each verified activation's original flow/iteration information.
Collapsed flows receive canvas state only when all activation slots are present
and unanimous. Mixed activity iterations likewise do not add contradictory
canvas markers; their individual table states remain visible.

Focused36cases passed3.72s; broad1187passed/13explicitPGskip/1deselected91.76s;
frontend69passed3.21s; all22actual container/browser cases passed68.64s with owned
cleanup. Changed-dependency TypeScript also passed. SRP: source lineage and pure
canvas aggregation are focused collaborators, while broad existing CaseFlow/
editor orchestration retains coupling debt. Canonical traversal event coverage,
complete original-edge correlation, browser Registry and full UI command recovery
remain open. Report: `artifacts/test-gates/bpmn-caseflow-source-provenance.v1.json`.


### Canonical Hub edge decisions and terminal observations (2026-10-13)

The Hub scheduler now emits bounded source-bound BPMN edge observations through
the existing fenced canonical emitter: selected/skipped/blocked decisions and
actual completed/failed target states. Selection remains distinct from Task
execution or completion. Stable plan/attempt dedupe keys support retries without
manufacturing results. CaseFlow admits these facts only with canonical schema,
Hub actor, exact tenant/workflow/run/plan/definition/direction and strict attempts.
Explicit current edge outcomes take precedence over older gateway selections.

Real containers exposed an existing replay gap: a repeated durable observation
with a new clock value could lie below the checkpoint cursor. The appender now
uses the existing exact dedupe read port after the normal fenced append conflict.
Only identical intent already covered by the cursor is reused; its first time
is retained. Changed intent and expired leases still fail, and this path does
not rebase over another control transition. Memory and actual SQL tests cover
clock changes/content changes; a signed lease test refuses stale replay.

The final PostgreSQL Full-App gate checks130edge observations across13references
in54.52s, including complete actual-plan edge coverage for completed cases. The
22case container/browser regression passed113.97s; both owned cleanups verified.
Focused120cases passed18.19s, frontend70passed3.51s and changed-dependency
TypeScript passed. The stable report records the final broader regression.
An older recovery assertion now counts the single canonical gateway decision
explicitly, rather than newly additive target-related edge events.

SRP: edge observation and wire validation use focused collaborators; existing
broad scheduling/projection remains preserved coupling debt. Cancel-specific
edge settlement, complete UI command recovery, browser Registry and optional/
productive admission remain open. No deployment or production proof.
Report: `artifacts/test-gates/bpmn-canonical-edge-events.v1.json`.

### Pause, cancellation and command recovery (2026-10-13)

Signed Hub cancellation now closes unresolved BPMN edges through the existing
fenced emitter. Completed, skipped and previously failed outcomes are preserved;
unresolved steps are projected as cancelled. SQL crash recovery with a later
clock retains the first durable occurrence without duplicate settlement.

The editor exposes Pause through the same owner-authenticated control path as
Resume and Retry. A real browser run found two additional integration gaps:
event preflight accepted a missing project that Native start rejected, and public
compatibility checkpoint references could not bind actual Native commands.
Preflight now shares the pure start scope guard, and the editor supplies an
explicit project with readiness invalidation. An additive closed command binding
retains the real signed checkpoint and execution revision; public compatibility
fields remain available. Receipts admit this bound metadata and validate progress
against the execution observation, never a caller-provided unbound counter.

Unchanged BPMN ticks reuse a verified checkpoint after current lease/revision
checks. Event, result, wait consumption and command changes still create a new
checkpoint. Receipt admission can retry an unexecuted contention at most five
times with at most 0.75 seconds of backoff. Each attempt rechecks the same scope,
checkpoint, revision, expiry and nonce. No executed effect, consumed nonce,
changed snapshot or competing command is silently rebased.

The focused real Chromium gate passed after login, Pause, full page reload,
reauthentication, definition recovery, Resume and Cancel. It verifies three
unique cancelled source edges, no delegated timer successor, and disabled Retry
for the closed timer. Multiple SVG representations of one edge are not separate
BPMN activations. The report records the final wider checks separately.

SRP is protected by focused edge and command-binding collaborators. The broad
Native command applier, projection and SQL receipt adapter remain existing debt;
the wait seam is injected for deterministic headless contention tests (DIP).
Positive service/approval Retry recovery and browser Registry reservation remain
open. No deployment or production release proof is claimed.
Report: `artifacts/test-gates/bpmn-pause-cancel-command-recovery.v1.json`.

Final validation: all22core/browser cases passed in130.02s with owned cleanup;
1387backend cases passed (13skipped,1deselected), followed by228cases covering
the bounded public-publication follow-up. The frontend76cases and changed
dependency TypeScript check passed. The earlier current PostgreSQL idle-state
regression passed13references,130edge observations and16Worker Registry runs;
its scope precedes the additive public command binding follow-up and is not
represented as a new productive release proof.

### Positive Retry recovery and reopened edges (2026-10-13)

Retry previously reused pre-cancellation edge observations when neither endpoint
had consumed another Worker attempt. This left cancelled arrows on a reopened
approval run. The Hub now persists a strictly validated BPMN edge epoch, emits
pending facts for unresolved transitions and uses the new epoch for subsequent
selection observations. The immutable definition and plan revision are retained.
Completed targets, skipped branches and remaining failures are preserved.

Another recovery gap left a retried run marked running when it already had an
open gate. The existing gate now restores waiting-for-approval or paused status
without a duplicate request and without granting approval. Actual SQL tests
cover crash-after-event recovery, exact retry replay and preservation of a
confirmed service result without another Worker execution of that step.

Authenticated Chromium exercises cancellation, full reload/login, definition
recovery, Retry and another full reload of the reopened approval run. The Hub
observes one retry, three pending edge facts and zero unapproved Worker tasks;
the browser observes no stale cancellation markers. Timer Retry remains denied.
The test Hub must declare ROLE=hub for the existing uncertain-dispatch policy;
its initial missing-role rejection was not bypassed or weakened.

The focused browser gate and wider regressions are recorded in
`artifacts/test-gates/bpmn-retry-edge-recovery.v1.json`. SRP remains protected by
the dedicated edge collaborator. Broad scheduler/state serialization and legacy
attempt coercion remain preserved debt. Browser pre-execution Registry coverage,
productive provider/release acceptance and full frontend packaging remain open.

The final complete22case core/browser gate passed in147.21s with verified owned
cleanup. The backend1394cases (13skipped,1deselected), frontend77cases and changed
dependency TypeScript check passed. These results cover the local synthetic
retry path; they do not satisfy a productive release gate.

### Pre-reserved browser Registry assignment (2026-10-13)

The isolated Hub now creates an actual SQL acceptance Task, ownership lease and
Worker-assignment row before issuing its browser projection. Only the Hub
Evidence Registry registers the immutable synthetic input/source and reserves
the test RUN. The browser receives the closed assignment from its scoped
bootstrap endpoint before Chromium launch and echoes it in its bounded report.

The Hub first checks actual Native tasks/control events, then locks the current
Task, assignment and ownership rows to validate scope, worker, fence, expiry and
unchanged input. Changed IDs, inputs, scopes, leases and cancelled Tasks remain
uncompleted. Exact completed-result replay checks the original result rather
than issuing a new assignment. Synthetic scope is explicitly denied by the
production release verifier.

Host snapshot identity now includes the mounted frontend source/config and is
rechecked after browser execution. Docker resolves the cached browser image to
an immutable image ID, which is both used for Compose and bound into the run
profile. No browser/Worker code mints source or run evidence IDs.

The full22case core/browser gate passed, followed by43focused negative/runner
tests and a final focused browser gate for the cancelled-Task guard. Wider1412
tests passed before that final guard; the report preserves these scopes.

The bootstrap is a dedicated synthetic acceptance-executor admission, not
productive Worker registration. Its RUN covers Chromium execution, not earlier
bundle compilation or every independent component test. Productive provider,
packaging, rollout and release admission remain open. SRP separates browser
coordination from the Registry adapter; SQL/Flask fixture composition remains
preserved coupling debt. Report:
`artifacts/test-gates/bpmn-browser-registry-reservation.v1.json`.

### Full application frontend verification (2026-10-13)

The full application TypeScript check and Angular build pass using the actual frontend dependency tree and Game Core source in a nonroot, network-disabled, read-only container. Build outputs and Angular cache stay in bounded tmpfs. This closes the previous local missing ECharts/Game Core verification gap. A fresh lockfile installation was not exercised; existing unused-import and CommonJS optimization warnings remain. This is a technical observation, not production release evidence. See `artifacts/test-gates/bpmn-full-frontend-build.v1.json`.
