# BPMNX-007/014 isolated container and browser acceptance

Run from the repository root with the already cached backend test image:

```sh
python3 scripts/test_bpmn_container.py
python3 scripts/test_bpmn_container.py --browser --timeout 240
python3 scripts/test_bpmn_container.py --full-app
```

Optional: `--timeout 180 --image ananta-backend-tests:local --report /tmp/bpmn-core.json`.
The report path must not exist. Without `--report`, a private temporary JSON file
is created and its path printed. A failure, missing acceptance report, timeout,
or incomplete cleanup returns nonzero. This is an opt-in executable acceptance
suite, not collected by the repository-wide synthetic pytest fixtures.
The optional browser uses the cached `ananta-frontend-tests:local` image;
`--browser-image` changes that image without pulling or building it. Repeat
`--case NAME` to select individual cases. Such reports explicitly set
`partial_case_selection=true`; selecting a subset never claims the complete gate.

`--full-app` selects a separate container profile and cannot be combined with
`--browser` or `--case`. It calls the real `create_app(testing=False)` without
bootstrap doubles, composes the SQL-backed Hub Memory runtime, verifies route
registration, real health/login HTTP and unauthenticated Native/workflow denial,
and confirms no unauthorized task was persisted. Only its required read-only
package, prompt, GeoMap and SFU/LiveKit configuration mounts are added; ordinary
Hub/Worker fixture mounts remain narrow. Mandatory startup policies stay enabled.
Unrelated background schedulers use the existing explicit disable setting.
This gate proves full application bootstrap, not positive Autopilot dispatch,
outbox recovery, production packaging or release admission.

`--full-app-autopilot` now runs thirteen compiled reference scenarios after the
real `create_app(testing=False)` bootstrap: service and artifact handoff, both XOR
branches and default, bounded loop, subprocess, explicitly approved/rejected/
blocked UserTask, timer, message catch, and AND join. The Hub uses its actual
authenticated `/tasks/autopilot/tick`, persistent queue/assignment/grants and
result admission; the deterministic Worker remains a separate container.
Assertions compare persisted Tasks, accepted dispatch journals, Native receipts,
selected edges, projected values and canonical event order. The AND case observes
a pending sibling while one actual HTTP tick runs asynchronously, releases that
Worker task through the isolated fixture port, and verifies the successor starts
after both sibling results. Every future and HTTP call is bounded and cleaned.

The UserTask decisions are explicit synthetic headless Hub policy commands.
Missing policy produces zero Tasks and a bounded `waiting_for_approval` observation;
it is not treated as a successful approved execution. Timer/message assertions
require exactly one canonical catch-consumed event. All thirteen observations
remain technical/synthetic evidence; they do not establish productive release
admission, optional backend acceptance, or the complete PostgreSQL matrix.
`--full-app-tool-isolation` retains its focused two-scenario boundary scope.

Full-application reference Worker Tasks now use the actual Hub Evidence Registry.
The host records real HEAD plus a bounded digest of the selected mounted source
families (including uncommitted files), and rejects a source change during the
owned run. Each scenario has an actual active SQL control Task/project. Before
signing or dispatch, the Hub validates current assignment/grant/lease, admits
synthetic task input and reserves a test-scope RUN in the same transaction as
adding its closed projection to the Task. Only the Registry issues SRC/RUN IDs.
The separate Worker validates and returns that exact projection. The Hub checks
its admitted Native receipt, records the actual result digest, verifies the
registered assignment result, compares reservation/Worker timestamps, and proves
that production release verification rejects the synthetic run. The thirteen
reference cases currently exercise sixteen separately reserved Worker runs.
Blocked/rejected UserTasks have zero Worker Tasks and therefore no Worker RUN;
the headless controller observations are still technical test observations.

The standalone Compose project has a fresh random name, an internal network,
no published ports, no Docker socket and no external services. All processes
run as an unprivileged user with read-only roots, dropped capabilities, memory,
CPU and PID limits. Each container owns its own tmpfs runtime data and secrets;
the worker cannot read the Hub database or signing key. Narrow source/schema
mounts are read-only. The repository root, `.env`, production secrets and runtime
data are not mounted. Docker images are never pulled or rebuilt. No command
targets compose-next, minipc, or any pre-existing Compose project.

The runner bounds the entire run (default 180 seconds, configurable 30–600),
terminates the Compose client on timeout/interruption, and tears down only its
exact project. It verifies no project containers or networks remain. Every
HTTP call has a timeout; each acceptance run has a 25-second / 250-tick bound.
Cleanup gets its own bounded allowance beyond the run timeout. SIGKILL or host
failure cannot run Python cleanup; the printed project name permits exact
manual cleanup in that exceptional case.

## What is real

1. Production BPMN XML import, semantic compilation, `WorkflowRequest` and
   `ExecutionPlan` adaptation, and `NativeGraphOrchestrator` control nodes.
2. `AnantaHubTaskQueueAdapter`, `TaskQueueService` ingestion and dispatch queue,
   task repository, and task status transitions in a private SQLite database.
3. Production strict registration validation, persisted worker registry,
   assignment binding, SQL ownership leases, and `WorkflowWorkerGatewayService`.
4. Cross-container HTTP through `HttpWorkflowHubDecisionClient`, production
   registered-worker authentication, signature and lease/fencing checks.
5. `WorkflowAdapterTaskConsumer`, `NativeGraphWorkerTaskAdapter`, and
   `NativeDelegatedNodeRuntime` with an injected deterministic node handler.
   Only public Ed25519 verification material crosses into the worker.
6. Production `consume_delegated_workflow_task` result envelope,
   `persist_forwarded_execution`, Native queue polling, result binding and
   ownership acknowledgement. SQL checkpoints/events and signed policy commands
   remain production implementations.
7. Production `/login`, password hashing, persisted users and refresh tokens,
   signed Hub session JWTs, strict public workflow authentication, XML import,
   compilation, public start/status routes, workflow-control facade, Native
   bridge, SQL bindings and reconciliation.
8. With `--browser`, Chromium operates the actual Angular BPMN editor and
   `VisualProcessApiService`. A minimal fixture login form submits to real
   `/login`; a test interceptor carries the resulting JWT. HTTP is proxied
   directly to the isolated Hub without API response doubles. This is not the
   full Angular shell, production login UI, or application startup.

The fixture Hub drives the real queue and chooses its single registered worker.
Small test HTTP intake/bootstrap routes host production services. The public
route composition lookup is explicitly injected with a facade built from
production components and a synthetic runtime-admission port. Its diagnostic
observer records exceptions and rethrows them unchanged. An explicit denial
case checks that synthetic admission denial creates no Tasks. This test port
does not read, create or satisfy production release evidence. Full Flask
application startup, Autopilot scheduling, generic `/tasks/.../execute` intake
and distributed dispatch outbox recovery are not covered. Hub and worker use
the same dependency image, with distinct
processes, filesystems and roles. This is service-level container acceptance,
not a production-stack or packaging acceptance test. No queue, assignment,
authorization or result acceptance service is monkeypatched.

## Assertions and interpretation

Cases cover both XOR outputs and default routing, AND join after both sibling
results, an explicitly signed allow/reject test policy, bounded blocked output
when automatic approval is absent, and HTTP rejection of missing assignment,
wrong worker identity and stale fence. Successful cases compare actual task
identities, assignments, result bindings, worker/Hub hostnames and canonical
events. The AND case requires observing a pending sibling and checks that the
downstream node starts only after both sibling results.
The second sibling uses a bounded, Hub-released test-handler barrier, so the
pending-sibling assertion does not depend on artificial sleep timing. This
fixture control does not create work or alter production authorization.

Additional result-boundary cases mutate the attempt or fence in a real Worker
HTTP result before production forwarding. Native rejects them before Task-result
persistence, canonical completion or ownership acknowledgement. The production
Task completion policy creates an immutable Hub-owned technical receipt; the
fixture uses its real SQL authority and Hub-only signing configuration.

Public cases independently exercise graph start, canonical-request replay,
SQL composition reconstruction, and graph-start replay. Reconstruction creates
new facade/bridge/orchestrator/store objects over the persisted database; it is
not a container restart or durable-key recovery test. `PublicScenario` accepts
XML and expected delegated node IDs, allowing additional approved capability
fixtures without changing transport, authentication, or admission services.

Definitions, data, execution output and policy fixtures are synthetic. The
handler performs no tools or LLM calls. Bounded loop/subprocess cases verify
explicit projections, distinct activation IDs and resumed SQL state. Timer and
message cases verify persisted catch application before successor dispatch.
A zero-duration container timer avoids timing-based sleeps; injected clocks in
the component suite cover deadlines and cancellation races.

The full local run passed **22/22 cases**, including real login and Chromium,
with complete cleanup. Ten host runner-safety tests pass separately.
This is not a full application or production release gate.

`artifact_handoff` executes a producer and consumer across the isolated Hub/
Worker boundary. The producer stores real synthetic bytes using the immutable
local writer; the reader and HTTP publisher verify/transfer those bytes through
production registered-worker authentication and Native artifact ingress. Hub SQL
binds admission to the current Task, assignment, attempt, fence, dispatch lease,
declared output and hash. Task completion requires the exact technical receipt.
The consumer downloads and verifies the admitted bytes through the production
Native input route. Hub and Worker filesystems/databases remain separate.

`artifact_unadmitted_output_denied` mutates a genuinely produced HTTP result to
reference an unadmitted artifact. Neither the Task's terminal result/receipt nor
ownership acknowledgement is persisted. The former blanket pre-start denial is
superseded for valid file declarations; missing declarations still fail closed.
Only the artifact runtime composition lookup is injected with a production
`NativeArtifactRuntime` using the test engine/key/storage. Routes, authentication,
authority, byte storage, admission and result validation are not replaced.

The initial graph hash and audit-correlation failures are fixed in production:
BPMN compilation is stable, and the closed delegated command carries the Hub's
correlation identity. Public start/replay and browser terminal checks pass
without response shims or relaxed identity validation. The browser fixture
waits for each paired status/history read to settle and checks the overall Hub
status, not the presence of any completed step.

Native result ingress now validates before forwarding projections and again in
the Task transaction, which creates a Hub-owned technical receipt. The hostile
attempt/fence cases assert that rejected Worker results do not change the Task.
The full unselected container/browser gate passed on 2026-10-11: 22 cases in
67.00 seconds with complete project cleanup. The separate 57-test artifact/runner
regression passed in 10.14 seconds. These are synthetic technical observations.

Remaining scope includes full application/dispatch-loop/container-loss recovery,
PostgreSQL concurrency and production-image/locked-frontend builds. The narrow
browser composition can report CaseFlow trace unavailability; it still uses
verified canonical events and rejects mixed status revisions.

Reports retain `public_api_diagnostics`, per-case observations,
and `not_covered`. Runtime keys are not Evidence
Registry IDs; this harness invents no `SRC_*` or `RUN_*` and cannot promote
synthetic output into a production gate. See the
[completion review](../../docs/security/bpmn-completion-review.md).

## Integration finding and verified fix on 2026-09-24

The initial real chain executed and persisted worker output, but the Native
runtime failed to publish its terminal checkpoint when worker authorization
appended to the same canonical event stream. Example: the checkpoint stored
sequence 9, authorization appends `workflow.step.authorization_checked` at 10,
and `NativeGraphOrchestrator._emit` appends with expected sequence 9. The SQL
store correctly rejects this with `event_sequence_conflict:expected=9:actual=10`.
AND exposes the same issue with two authorization events (8 versus 10).

The acceptance suite fails on this regression. It does not silence authorization
events, update private checkpoint state, retry a partially committed node
transition, or substitute an in-memory event store. Failed-case
reports retain task/result/assignment facts, checkpoint cursor and canonical
events. At the initial verification, reject, blocked-policy and assignment-fence
cases passed; all five completion cases failed on this production integration
boundary. Shared production code is outside this harness's ownership.

Parent integration subsequently added `native_graph_event_appender.py` and wired
both Native append paths to bounded CAS catch-up over independent Hub gateway
observations. Rerunning the unchanged container assertions passed **8/8 cases**
in 22.24 seconds, with exit code 0 and complete project cleanup. The SQL event
stream still includes the authorization observations. No fixture bypass was
needed. The harness also verified actual SIGTERM cleanup during startup (6.79
seconds, runner exit 130, complete cleanup). These timings are local technical
observations; they are not release evidence or performance guarantees.

The narrow happy-path result does not establish crash-atomic ownership/event/
checkpoint commits, concurrent graph-control recovery, or persistent read-grant
revocation. Those remain separate coverage obligations.

## Structure and local checks

`fixtures.py` owns BPMN inputs; `transport.py` bounds test HTTP; `worker.py`
owns execution composition; `hub.py` composes Hub services and dispatch;
`acceptance.py` owns assertions/reporting; `public_api.py` composes and checks
public workflows; `result_boundary.py` owns result fault injection;
`artifacts.py` creates and verifies the deterministic artifact regression;
`browser_hub.py` coordinates the bounded browser gate and `browser.mjs` drives
Chromium. The host script owns Docker lifecycle.
Dependency injection preserves the production queue and handler ports (DIP/OCP).
Existing SRP/DIP debt remains: the production `TaskQueueService` imports a route
package for queue ordering, which transitively loads unrelated retrieval
services. The isolated image therefore also needs the read-only CodeCompass
registry/schema. A focused queue-ordering service would remove that coupling;
the harness does not refactor shared code.

Host-runner safety checks (standard library only):

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/bpmn_container -p test_runner.py -v
```

Those unit tests use process doubles only for runner cleanup/configuration
safety. They are not reported as real container-chain acceptance.
Runner checks also cover browser opt-in, cached frontend-image inspection,
separate ephemeral bootstrap credentials, and explicit partial-case reporting.

The final matrix includes the stronger artifact denial: all duplicated result
references remain consistent, but point to the actually stored Worker-local
artifact. Rejection must be `native_artifact_hub_receipt_required`, proving the
Hub registration boundary itself. Additional file/transfer/input/Todo tests:
92 passed in 8.42 seconds; together with the 57-test suite, 149 targeted tests
passed. The curated stable report is
[the gate record](../../artifacts/test-gates/bpmn-native-artifact-container.v1.json).

## Full application Native Autopilot

```sh
python3 scripts/test_bpmn_container.py --full-app-autopilot
```

This separate opt-in profile boots `create_app(testing=False)` and sends
real authenticated `/tasks/autopilot/tick` requests. A separate Worker executes
one Native node and a producer/consumer artifact workflow. Production queue,
assignment/lease validation, Ed25519 command projection, artifact byte ingress
and SQL result receipts remain active. The fixture supplies synthetic graph
admission and deterministic execution handlers; it exercises no provider or
production release admission. Background scheduling is explicitly disabled
so the bounded HTTP ticks own test progress.

The Worker receives public verification keys only. Each closed projection has
an exact Worker recipient and short expiry; modified projections are denied.
The ordinary container/Chromium suite remains a separate 22-case gate.
Runner safety tests also cover mode exclusivity and missing acceptance reports.
The stable record is
[the Autopilot gate](../../artifacts/test-gates/bpmn-full-app-native-autopilot.v1.json).
Durable outbox recovery, actual container loss, PostgreSQL concurrency and
production packaging remain separate acceptance work.

The Full-App Autopilot gate also requires each dispatched Task's durable Hub
journal row to be `accepted`. Independent-engine SQL tests cover restart,
concurrent reservation and receipt reconciliation. These are not a substitute
for actually terminating a Worker container during execution.

## Actual Worker container loss

```sh
python3 scripts/test_bpmn_container.py --full-app-worker-loss
```

This mutually exclusive Full-App mode terminates the isolated Worker process
with exit 73 after real Native consumer execution and before its HTTP result
response. Compose starts detached so this expected loss cannot stop the Hub.
The host follows only Hub logs, bounds execution, inspects both container exit
states before cleanup, and requires a successful Hub gate plus the exact Worker
exit. Missing reports/identities, wrong exits and timeouts fail with cleanup.
No Docker socket is mounted into either container.

The Hub must persist an `unknown` journal outcome, omit a completion receipt,
and remain responsive for two follow-up authenticated Autopilot ticks. A
fixture-driven Hub status reset uses the normal Task repository; it cannot
create another send claim. Bounded reconciliation returns `unresolved` and the
workflow remains incomplete. All policy, assignment, lease and signature checks
remain active. Graph admission and execution handlers are explicitly synthetic.
This proves loss containment, not result redelivery or eventual completion.

The Native HTTP fixture now composes the productive private SQLite result
store in its own Worker tmpfs. The normal Full-App gate exercises mandatory
publication before response. This tmpfs does not prove persistence across
container recreation; a Worker-owned persistent test volume and dedicated
redelivery gate remain the next harness extension.


## Worker recreation with persisted result redelivery

```sh
python3 scripts/test_bpmn_container.py --full-app-worker-redelivery
```

The separate mode commits a result to a Worker-owned Docker data volume,
terminates the Worker before HTTP response, verifies Hub containment and asks
the host to recreate only that Worker. A private per-run nonce gates the host
request; duplicate/foreign/cancelled requests cannot initiate recreation.
The host requires old exit 73 and a different running container identity.
No Docker socket or restart credential is supplied to the Worker.

The host first runs a networkless ephemeral initializer with only CHOWN
capability to set 0700/uid 65534 permissions on the fresh Worker data volume.
It mounts no source files or Hub state. Application Workers remain unprivileged.
A tmpfs-backed Docker volume was explicitly rejected by this gate because its
contents disappeared at the last container stop; the final gate uses the
ordinary persistent local volume driver. All volumes are removed and checked
at cleanup. A leftover volume fails otherwise successful acceptance.

The complete Hub retrieves the old Worker's signed result and takes it through
production forwarding/SQL admission. The original result hostname must differ
from the new Worker hostname; Task, journal and workflow must complete without
another send claim. Synthetic graph/handler/status-reset fixtures remain
explicit; lease-expiry recovery, scheduler wiring and release admission are
separate work. The
[stable gate](../../artifacts/test-gates/bpmn-worker-recreation-redelivery.v1.json)
records exact scope and normal-mode regression.

The recreation/redelivery scenario now explicitly enables the Hub recovery
policy after Worker replacement and invokes authenticated `/tasks/autopilot/tick`.
It requires `recovered=1`, `dispatched=0` and completed Task/journal/workflow;
it does not call the recovery service directly. The default-off policy preserves
other modes and manual review behavior. See the
[scheduler gate](../../artifacts/test-gates/bpmn-native-result-scheduler.v1.json).

The separate expiry/retry gate is:

```sh
python3 scripts/test_bpmn_container.py --full-app-expired-retry
```

After durable result publication and actual Worker recreation, an explicit SQL
fault fixture expires the execution lease. Old result admission stays denied.
Signed production-orchestrator controls cancel the transport-review Task and
reject retry without Hub policy. An exact bounded Hub binding permits a fresh
Task/attempt with a higher fence; authenticated Autopilot HTTP completes it.
The persistent fault marker ensures only the original publication loses its
response. All containers, networks and volumes must be removed at cleanup.
This mode is mutually exclusive with other full-application modes, browser
execution and individual case selection. Synthetic handler and graph inputs,
injected SQL expiry and programmatic signed controls are explicit test limits.

Worker result retention tests additionally exercise startup and request-time
scrubbing, bounded batches, unknown execution expiry, live result preservation,
retained replay fences/quota and the finite maintenance module as a real
headless subprocess. The isolated cached test image runs the storage suite
with no network, UID 65534, dropped capabilities, read-only source mounts and
temporary private storage. This tests the shared-contract packaging; it does
not establish that the standalone Native Worker image can exclude `agent`.
The full application Worker-recreation/redelivery mode also remains green
after adding startup maintenance.

The Shell/private-store boundary has its own full application gate:

```sh
python3 scripts/test_bpmn_container.py --full-app-tool-isolation
```

The actual Hub dispatches native and artifact-handoff tasks into a separate
Worker. Static adversarial commands use the production Shell port; same-UID
read/write/truncate/chmod of its actual result database must fail. Workspace
writes and normal result/artifact admission must complete. Landlock, its
disconnected-directory fix and metadata Seccomp are mandatory. The fixture
graph/handler remains synthetic. Other process launch ports, network egress
and production release policies are not established by this gate.


### Owned PostgreSQL full-application gates

```sh
python3 scripts/test_bpmn_container.py --full-app-autopilot --postgres
python3 scripts/test_bpmn_container.py --full-app-worker-redelivery --postgres
python3 scripts/test_bpmn_container.py --full-app-expired-retry --postgres
```

`--postgres` is available only for full Native application modes. The runner
requires an exact cached SHA-256 image, starts its own health-checked database
before the application and removes it with the same random Compose project.
The database has no host port or persistent host storage. Its credentials are
explicit synthetic fixtures. Host database configuration is never inherited.
The transport guard admits only the exact owned database URL for the explicitly
enabled Hub; both Worker services retain their private SQLite URL. Invalid
mode/image combinations fail before creating a report or Docker resources.

The reference matrix reserves sixteen actual Hub Registry test RUNs before
Worker execution across thirteen scenarios. PostgreSQL redelivery/retry gates
exercise real Worker recreation, current lease admission and bounded exact Hub
retry policy with pre-reserved Registry identities. These remain synthetic technical
observations; no test identity can satisfy a production release gate.

Startup failures include bounded traceback locations without exception text,
local variables or source lines, so diagnosis does not expose credentials.
See `artifacts/test-gates/bpmn-postgres-full-app.v1.json` for verified scope.


### Pre-execution Registry runs for Hub control

The normal full-app reference gates decorate the actual signed Hub control-lease
service through a narrow optional orchestrator port. The decorator acquires the
original lease, reserves an actual Hub Registry test RUN under that lease owner
and checkpoint, and only then permits start/advance/resume. Its immutable source
binds plan, input, parallel limits and the complete signed approval/message
command. It does not manufacture a control lease or replace runtime policy.

After the action, the decorator verifies the actual persisted signed workflow
checkpoint and completes the exact registered assignment/result. Changed input,
command or SQL Task scope prevents successful completion. Registry admission
failure rolls back source/run and stops the action. All successful test RUNs
must fail production release verification. A successful control action may
leave the workflow blocked, rejected or waiting; its RUN does not claim workflow
completion. Poll counts are timing-dependent, not a capability requirement.

The control decorator covers all thirteen normal references, including the
zero-Worker blocked/rejected cases and timer/message operations. Worker fault modes also have pre-reserved Registry coverage; browser acceptance
still requires its own Registry integration. See `artifacts/test-gates/bpmn-hub-control-registry.v1.json`.


### Registry identities through Worker fault recovery

Full-app Worker-loss/redelivery/expired-retry modes now also compose actual Hub
control and Worker Registry reservations. The lost-response stage verifies the
original RUN's closed assignment remains reserved and has no admitted result.
Redelivery completes that same RUN only after actual Hub result admission.
Expiry/retry leaves the original RUN uncompleted and reserves a distinct RUN
before dispatching the fresh higher-fenced attempt. A refused control invocation
is also observed as reserved; it never acquires a fictional successful result.
Reserved runs fail the Registry's successful-result requirement, while completed
test runs fail its production-scope gate.

These scenarios provision actual SQL control Tasks/projects through a focused
fixture helper. Control ownership remains in the Native command; a control Task
is not inserted as a generic execution dependency. The unknown-dispatch reset
case exposed a real state-machine defect: pre-execution refusal could not move
`todo` into `waiting_for_review`. The canonical review transition now supports
pre-execution states without force or pretending work started; terminal/reserved
Tasks cannot be reopened by that transition. Existing approval rules remain.

Both redelivery and expiry/retry passed on isolated SQLite and PostgreSQL with
actual Worker recreation and complete owned cleanup. Stable scope and counts:
`artifacts/test-gates/bpmn-registry-fault-recovery.v1.json`. Browser Registry,
full UI recovery/commands and optional/productive acceptance remain separate.


### Persisted definition and actual browser reload

The authenticated Chromium case now reloads the complete page, logs in again,
loads the original edited BPMN source from the new owner-only
`GET /api/visual-process/workflow/<id>/bpmn-definition` endpoint and restores the
Hub's completed state. It requires exactly one start request and two actual
Worker Tasks. The editor offers `Lauf wieder laden`; it does not restore state
from browser memory or treat source retrieval as start authorization.

The closed source projection verifies byte SHA and the original canonical plan
and policy binding. Public workflow aliases may differ from the admitted BPMN
process name. Hub read authorization requires no live runtime query or new task.
Browser recovery verifies typed IDs/hashes, bounded bytes and WebCrypto digest,
ignores obsolete responses after draft edits, and compares current run/plan/
definition identities before displaying state or enabling controls.

The current gate passed22cases67.55s with owned cleanup. Backend1192PASS,
frontend67PASS, and changed-dependency-closure TypeScript passed. Full-app
TypeScript in the cached image lacks ECharts and Game-Core dependencies; no full
frontend build is claimed. Browser Registry reservations and the complete UI
command/CaseFlow matrix remain open. Scope:
`artifacts/test-gates/bpmn-definition-browser-recovery.v1.json`.


Expanded CaseFlow edges now expose source-bound original element/direction,
activation, scope and iteration, with no raw variable or source payload dump.
The source projection verifies the immutable admitted XML and plan/policy; a
catalog with changed edge direction is rejected. Source lineage alone leaves
missing event correlation unverified. The UI shows individual activations and
avoids colouring collapsed edges with missing/mixed slots or activities with
contradictory iteration states. The current22case browser/container regression
and focused source/trace tests pass; canonical traversal coverage remains open.
See `artifacts/test-gates/bpmn-caseflow-source-provenance.v1.json`.


The Full-App reference matrix now checks canonical Hub edge event bindings and
requires every actual plan edge to be observed for completed cases. Edge choice
and actual terminal target outcome remain distinct. Exact replay preserves the
original durable timestamp and refuses changed content or stale control lease.
CaseFlow validates the complete Hub/run/plan/definition/direction binding before
using these new facts; older gateway selections cannot overwrite current trace
outcomes. Cancel settlement and the full UI command matrix remain separate.
See `artifacts/test-gates/bpmn-canonical-edge-events.v1.json`.
