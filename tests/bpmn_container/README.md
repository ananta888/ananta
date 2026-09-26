# BPMNX-007/014 isolated container and browser acceptance

Run from the repository root with the already cached backend test image:

```sh
python3 scripts/test_bpmn_container.py
python3 scripts/test_bpmn_container.py --browser --timeout 240
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
HTTP result before passing it through production forwarding. Native must reject
it without completing the canonical step or acknowledging ownership. This also
documents a narrower existing weakness: generic forwarding has already stored
the untrusted result and terminal status on the Task row when Native rejects it.
These tests do not claim that forwarding itself is an authenticated result
ingress contract.

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

The full local run passed **21/21 cases**, including real login and Chromium,
with complete cleanup. Seven host runner-safety tests pass separately.
This is not a full application or production release gate.

Artifact execution is deliberately unavailable: the earlier positive case
exposed a false completion with a Worker-local artifact ID and no Hub bytes.
The production plan validator now denies artifact-bearing BPMN before queue
admission. `artifact_unadmitted_output_denied` proves that rejection and zero
created Tasks; it does not pretend the missing positive transfer is complete.
The future ingress contract must bind assignment/attempt/fence/declared output,
content size/hash and an immutable Hub receipt. Existing Recovery ingress
requires different authority and is not an ordinary Native-node upload port.

The initial graph hash and audit-correlation failures are fixed in production:
BPMN compilation is stable, and the closed delegated command carries the Hub's
correlation identity. Public start/replay and browser terminal checks pass
without response shims or relaxed identity validation. The browser fixture
waits for each paired status/history read to settle and checks the overall Hub
status, not the presence of any completed step.

Remaining scope includes generic result-ingress admission before Task mutation,
full dispatch-loop/container-loss recovery, positive artifact transfer,
PostgreSQL concurrency and production-image/locked-frontend builds. The narrow
browser composition can report CaseFlow trace unavailability; it still uses
verified canonical events and rejects mixed status revisions.

Reports retain `public_api_diagnostics`, per-case observations,
`missing_production_contracts` and `not_covered`. Runtime keys are not Evidence
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
