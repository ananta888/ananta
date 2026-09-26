# BPMN Blueprint Editor

The Angular BPMN editor is available at:

```text
/process-designer/bpmn
```

It uses `bpmn-js` as the diagram editor and the hub API as the source of truth
for Ananta semantics.

## Data Flow

1. The user edits a BPMN diagram.
2. The editor exports BPMN XML from `bpmn-js`.
3. The hub imports XML through `/api/visual-process/bpmn/import`.
4. The hub returns a `VisualProcessGraph`, validation warnings and an
   `execution_support` report with element-specific reasons.
5. The editor can compile the graph through `/api/visual-process/workflow-request`.
6. Successful compilation triggers authenticated
   `POST /api/visual-process/workflow/preflight` for the same graph and options.
7. Only a complete positive preflight for the current editor snapshot enables
   Start. The editor submits that snapshot through
   `/api/visual-process/workflow/start`; the Hub still owns admission.
8. Status, canonical events and the existing edge-trace endpoint reconstruct
   the runtime view. Commands return to the authenticated Hub control routes.

BPMN XML is an import/export representation. The canonical execution contract
is `ExecutionPlan`; `WorkflowRequest` carries the additive, versioned
`execution_graph` projection. Client support reports are not runtime authority.

## Editable BPMN Elements

The backend adapter currently maps:

- `StartEvent`
- `EndEvent`
- `Task`
- `ServiceTask`
- `UserTask`
- `ScriptTask`
- `BusinessRuleTask`
- `ExclusiveGateway`
- `ParallelGateway`
- `SequenceFlow`

The current XML additions also parse intermediate catches and lower constrained
embedded subprocesses and standard loops. Their implementation boundaries and
unconfirmed acceptance status are described below; appearing in the modeler or
the compiler catalog does not establish runtime readiness.

Unsupported elements may be omitted from the graph preview, but their original
XML is retained and revalidated. Unknown execution semantics block compilation
and start. Normalized backend export refuses to discard them silently; the
modeler's original XML remains editable/exportable.

## Ananta Metadata

Ananta-specific fields are stored in `ananta:metadata` extension elements through
an explicit moddle descriptor. There is no independent component sidecar:

- task kind
- role
- gate flag
- policy scope
- allowed tools

The hub validates policy scope before creating an executable request. The UI
does not grant permissions by itself.

## Executable subset v1: status and activation

The core compiler and bounded extensions are integrated with Native. The isolated
21-case Hub/Worker/Chromium suite passes, including real authentication and
public graph start/replay. It composes production services with explicit
synthetic policy/admission and deterministic execution fixtures; it is not full
application boot, the full Angular shell or production release evidence.
Production remains **unverified**; open ingress/recovery boundaries remain in
`todos/active/todo.bpmn-execution-semantics-hardening.json`.

`ANANTA_BPMN_EXECUTION_ENABLED=0` is the default. Explicit opt-in (`1`) lets the
production Native candidate advertise `bpmn_control_v1`, `bpmn_activation_v1`
and `bpmn_events_v1`. It does not bypass
runtime health, release admission, tenant policy or approval. Legacy status-only,
Temporal and LangGraph runtimes do not advertise this BPMN contract. No
Camunda/Zeebe service is introduced; the Hub remains the sole control plane.

The authenticated `GET /api/visual-process/bpmn/capabilities` returns the versioned
catalog. Import returns `execution_support`; compile support is distinct from
`runtime_verified`, which remains false. Successful compilation is not a run.

| BPMN family | XML editing | Normalized exchange | Execution contract v1 | Production verified |
| --- | --- | --- | --- | --- |
| Plain start/end, task/serviceTask | Yes | Yes | Native, single-activation DAG | No |
| userTask | Yes | Yes | Delegated task behind Hub approval | No |
| exclusiveGateway, default sequenceFlow | Yes | Yes | First match; explicit XOR merge | No |
| parallelGateway | Yes | Yes | Acyclic split/join; all arrivals required | No |
| conditionExpression | Yes | Yes | Bounded ananta-condition-v1 only | No |
| scriptTask, businessRuleTask | Yes | Preview only | Rejected | No |
| One-shot intermediate timer/message catch | Yes | Original XML retained; bounded definition parser present | Integrated Native; isolated container case passes | No |
| Bounded standard loop, embedded subProcess | Yes | Original XML retained; bounded DAG lowering present | Integrated Native; isolated container case passes | No |
| Arbitrary sequence-flow cycles, callActivity | Yes | Original XML retained; preview incomplete | Rejected | No |
| Boundary, inclusive/event-based gateways, multi-instance, compensation | Yes | Original XML retained; preview incomplete | Rejected | No |

The tag/attribute/child allowlists live in
`agent/visual_process/bpmn_execution_support.py`. Foreign execution attributes,
unsupported event definitions, unknown extensions and unbound nested execution
semantics are rejected.
Diagram layout is not execution semantics. `isExecutable` is an editor hint, not
authorization. Ananta extensions must contain a single valid JSON object.

## Source binding and defined routing

XML -> bounded parser -> VisualProcessGraph -> WorkflowRequest execution_graph ->
ExecutionPlan -> Hub queue -> delegated worker tasks.

The parser rejects DTD/entities and duplicate IDs, and bounds UTF-8 size (1 MiB),
depth (64), and element count (4096); HTTP limits also apply. No network lookups
occur. Admission reimports original XML, checks topology, conditions and control
kinds, recomputes the source-definition hash, and revalidates direct requests.
ExecutionPlan binds edge IDs, conditions, controls, source hash and request hash.

- XOR evaluates non-default edges in XML document order; first true wins.
  Default applies only when no regular condition matches. Missing values, wrong
  types and no match without default fail with a reason code, not a delegation.
- Expressions allow scalar `input.*`, `results.*`, `artifacts.*` comparisons with
  literals: `== != < <= > >= in` and `and/or/not`. No executable calls, FEEL/DMN,
  model queries, files or network. Types match exactly; bool differs from int;
  missing differs from explicit null (`None`). Numbers must be finite. Length,
  depth and node limits apply. The existing three-valued evaluator makes a
  definite false in AND / true in OR decisive; otherwise unknown remains an error.
- Inputs bind at start. Selected edge IDs/results persist in signed checkpoints.
  Restart rejects changed inputs and does not reroute. Skipped branches propagate
  skips and create no worker tasks.
- Parallel splits activate every outgoing branch. Joins wait for every required
  arrival of the same run/node activation. Partial activation fails explicitly.
  Nested acyclic splits use the core DAG contract. The bounded XML loop
  transformation below creates distinct activations instead of revisiting a
  graph node; it does not enable arbitrary sequence-flow cycles.
  Results remain namespaced by node ID, with no shared last-writer-wins map.
  Hub concurrency, assignment, fencing and budget checks remain in force.
  Branch failure is terminal by default.
- XOR merges with multiple simultaneously active arrivals fail. Implicit
  multi-input task/end merges and combined split/merge gateways are rejected;
  model separate gateways. Arbitrary cycles/backedges and external subprocess
  calls remain rejected. Event and structured-region additions have separate bounded container
  cases; deployment readiness still requires their exact release/policy gates.
- User tasks return a bounded `waiting_for_approval` result without delegation.
  A signed Hub command subject to policy can approve/reject automatically.
  There is no compiler auto-approval and no mandatory interactive test step.
- BPMN run deadlines persist in signed checkpoints and include queue delays and
  approval waits. The Hub rechecks them before every dispatch; restart or a late
  approval cannot extend the deadline. Expiry cancels outstanding assignments
  before accepting late results. This is not a worker-side sleep or timer event.
- Metadata cannot turn a userTask into an ungated task or a gateway into a
  worker. Controls cannot own tools/artifacts. Task metadata remains untrusted
  input to Hub authorization, not a permission grant.

## Editor and runtime truth

Moddle XML exchange retains role, gate, policy hints, tools and unknown JSON
fields. Undo/redo, copying/deleting elements and clean XML roundtrips retain
those fields. XML textarea edits affect the diagram only after Import. An import
warning about possible data loss blocks Start until a clean document is imported.
Model edits invalidate compile/preflight eligibility and remove runtime canvas
markers; late responses cannot restore eligibility for an obsolete revision.
The bpmn.io watermark remains visible.

### Preflight and Start

The preflight request has the same graph/options body as Start. The editor uses
`{graph, policy_scope: {source: 'bpmn_blueprint_editor'}}`. The authenticated Hub
derives tenant and principal; the browser does not supply those authorities.
`BpmnWorkflowPreflight` uses the Hub's start-plan and selection/policy ports and
returns an advisory result without starting a workflow.

| Response field | Frontend requirement |
| --- | --- |
| `ready` | Exactly `true` before enabling Start |
| `workflow_id` | Must equal the submitted graph ID |
| `runtime_id` | Nonempty selected runtime; no frontend fallback list |
| `plan_hash`, `definition_hash` | Both 64-character lowercase SHA-256 digests, bound to the current request |
| `reason_codes` | An array; must be empty for a ready result |

Missing fields, a negative result, transport failure or preflight timeout leave
Start disabled. Reason codes are shown in the result panel; element-specific
import/compile issues can focus and mark the affected element. The support
catalog describes compiler contracts; it cannot enable Start by itself.

Start submits the captured graph plus `expected_plan_hash` and
`expected_definition_hash` from preflight. A returned status is accepted only
when its workflow, plan and definition match. The workflow ID remains available
for status lookup if a start acknowledgement is missing or inconsistent.
Neither compilation, positive preflight nor a `running` status is evidence of
Worker execution or release eligibility.

### Runtime reconstruction and canvas markers

Enter the workflow ID and use **Status laden** to reconstruct a run, including
after reopening the editor. The loader reads status, then canonical events and
`POST /workflow/<id>/caseflow-edge-trace`, then status again. The edge-trace body
is `{schema: 'ananta.caseflow_edge_trace_query.v1', run_id: <loaded run ID>}`;
the run identity is not placed in a query string.

The two status observations must agree on tenant, workflow, run, plan,
definition, revision, checkpoint and status. A concurrent change discards the
combined view and requests another load. A workflow selection change makes
older replies irrelevant. Individual reads, preflight and control requests have
15-second response bounds. This is manual reconstruction, not a live event
subscription or browser-owned runtime cache.

Step outcomes come from explicit Hub status fields. Canonical events add
delegated task/activation/iteration identities and explicit gateway
`selected_edge` decisions. Events are checked for workflow/run/tenant binding,
optional plan/definition bindings, event identity and the reported event cursor;
duplicates do not add duplicate edge rows. Older delegation events cannot
replace a task identity already present in the current status. Edge-trace rows
require the exact workflow/run/revision and a verified row. Its `active` or
`inactive` value is not promoted to workflow completion.

The view contains only selected identity/status fields, with no variable,
message, tool-output or full event-payload dump. Unknown identities and
activation fields stay absent. It never synthesizes `SRC_*` or `RUN_*` evidence.
Canvas markers require the runtime `definition_hash` to match the current
Hub-checked diagram. Without that match, the table remains a view of the named
run and the canvas stays unmarked. Known markers distinguish selected paths,
running, waiting, skipped, completed, failed, blocked and cancelled states;
unknown states remain textual. Reopening a run does not automatically restore
its original XML: import the appropriate definition to establish that match.

### Resume, Retry and Cancel

The buttons are implemented against existing authenticated Hub endpoints. They
are enabled only for commands explicitly included in the loaded status's
`allowed_commands`, with a complete run/plan/checkpoint/revision binding.
`running` or `paused` alone never implies command availability.

| Endpoint | Editor request body |
| --- | --- |
| `POST /workflow/<id>/resume` or `/retry` | `command_id`, `expected_revision`, `plan_hash`, plus `payload: {run_id, checkpoint_ref}` |
| `POST /workflow/<id>/cancel` | `command_id`, `expected_revision`, `plan_hash`, `run_id`, `checkpoint_ref`, `reason` |

All fields come from the loaded Hub snapshot. Command IDs are request
deduplication keys, not evidence identities. While a command is pending, further
controls are disabled. A successful acknowledgement triggers fresh Hub reads;
an inconsistent response, denial or timeout clears the actionable snapshot and
requires a status reload. The editor does not automatically replay an uncertain
mutation. Commands target the named immutable run, even when its definition
differs from the current editor diagram.

Preflight, start hash checks and command revision/plan/run/checkpoint validation
are integrated. Native publishes advisory command hints from its control policy;
the public projector defaults missing hints to an empty list. Hints never
authorize a command: route ownership and signed Hub decisions remain mandatory.

BPMN compilation now supplies a stable correlation identity, so identical graph
preflight/start/replay binds the same plan hash. The closed delegated command
also carries the Hub correlation to Worker authorization audit events; mixed
event identities are still rejected. Public graph replay and authenticated
browser execution verify this integration.

## Integrated bounded XML extensions

The following source-bound subset is covered by synthetic contract, recovery and
separate-container checks. This is not a deployment-readiness assertion.
Always use current Hub import/compile and authenticated preflight for a concrete
definition.

`bpmn_xml_regions.py` lowers constrained XML to the existing DAG. Embedded
`subProcess` requires one connected start/end and a `bpmn_region` object with
schema `ananta.bpmn_region.v1`, explicit `input_mapping` and `output_mapping`.
Children use explicit input projections. The transformer binds generated IDs
and origin/scope metadata to the original XML digest; it is not a second
runtime. Source admission repeats the transformation and rejects changed
compiler-owned mappings.

Standard loops require `testBefore=true`, a finite `loopMaximum` within the
default bound of 32, and a bounded `loopCondition`. The `bpmn_loop` metadata uses
schema `ananta.bpmn_loop.v1` with matching input/repeat state fields. The
condition may access only that projected input state. Iterations become distinct
nodes in a finite DAG with explicit state transfer. Arbitrary backedges,
test-after loops, multi-instance loops and `callActivity` are not enabled by this
transformation. Original region XML is retained for export.

The separate [activation expansion](../architecture/bpmn-activation-expansion.md)
component API remains a distinct preview boundary. Its pinned component
contracts must not be conflated with the new XML-region path or used to claim
external subprocess-call support.

`bpmn_event_definitions.py` parses a single timer or message definition on an
`intermediateCatchEvent`. Timers accept fixed ISO day/hour/minute/second durations
or dates with an explicit timezone; cycles, calendar durations and expressions
are rejected. Waits have a bounded timeout of at most 86,400 seconds; a duration
must be shorter than its timeout. Message catches require a named local BPMN
message declaration plus `bpmn_wait` metadata with a correlation key and a
closed scalar payload schema pinned by its digest. No remote schema lookup is
performed.

[Durable waits](../architecture/bpmn-event-waits.md), a Native wait adapter,
parent-run lease coordination and authenticated message ingress code are now
present. The message endpoint uses a closed command/revision/plan/step envelope;
generic signals cannot substitute for it. The BPMN editor has no message-send
control or dedicated loop/event mapping form: XML import/export preserves these
definitions, and Hub admission decides their eligibility. Timer/message SQL-recomposition cases pass in separate containers; injected-clock
component tests cover due/cancel races and replay. No all-backend or
container-failure guarantee follows from these tests.

## Compatibility, rollout and rollback

Dependency-only legacy WorkflowRequests keep their serialized format and plan
hash behavior. Conditional visual graphs cannot use lossy blueprint projection;
BPMN uses the canonical request path. Old saved BPMN graphs without source XML
must be reimported. Changed topology needs a new XML-bound definition, not edits
to an already running plan. No worker-to-worker orchestration is introduced.

Before rollout, close full-application/dispatch/container-loss acceptance gaps and obtain Hub-reserved,
correctly classified source/run identities. Local test commands are technical
observations, not production evidence. Disable the flag to prevent new runtime
selection; retain a compatible binary and persisted state to drain/cancel live
runs. Never downgrade BPMN plans into the legacy adapter or delete checkpoints.
Synthetic downgrade/drain/rollback-denial exercises pass. A production deployment,
PostgreSQL restore drill or any minipc rollout remains unperformed.

## Local headless checks

The cached backend image can run with no network and a read-only repository:

```bash
docker run --rm --network none --entrypoint python \
  -v "$PWD:/workspace:ro" -w /workspace \
  -e PYTHONDONTWRITEBYTECODE=1 -e DATABASE_URL=sqlite:////tmp/bpmn-tests.db \
  ananta-backend-tests:local -m pytest -o addopts= -q --tb=short \
  --confcutdir=tests/bpmn -p no:cacheprovider tests/bpmn
```

Frontend targeted tests, with the project's locked dependencies installed:

```bash
cd frontend-angular
npm run test:unit -- src/app/features/visual-process/bpmn-metadata.spec.ts src/app/features/visual-process/bpmn-blueprint-editor.component.spec.ts src/app/features/visual-process/bpmn-runtime-view.spec.ts src/app/features/visual-process/bpmn-workflow-api.spec.ts src/app/features/visual-process/visual-process-api.service.spec.ts
node scripts/test-bpmn-browser.mjs
```

The browser script uses real headless Chromium and bpmn-js/moddle with Hub API
doubles, not a live login or production backend. The cached frontend test image
contains older Angular/Vitest versions than the current lockfile; targeted tests
and TypeScript checks on that image are not a full locked-dependency build.
The latest frontend check completed 71 unit tests, eight Chromium scenarios and
targeted TypeScript checks. Chromium covers real modeler interaction, stale
preflight, fresh-component runtime reconstruction, definition-bound markers and
control request bindings using deterministic API doubles. Fresh-component
reconstruction is not proof of live login, Hub restart or Worker recovery. These
results are synthetic technical observations, with no production evidence claim.

Run isolated service-level container acceptance from the repository root:

```bash
python3 scripts/test_bpmn_container.py --browser --timeout 300
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/bpmn_container -p test_runner.py -v
```

The [harness contract](../../tests/bpmn_container/README.md) names the production
queue, assignment, lease, worker authorization, result and SQL checkpoint paths
used, plus boundaries it does not cover. The full 21-case container run is green,
including actual `/login`, public graph replay and the Angular editor. The
artifact case is explicitly **negative**: artifact-bearing plans are blocked
before Task creation until Hub-owned transfer/receipt admission exists. It does
not count as positive artifact acceptance.

Core and extension tests use no production credentials, external LLMs or human
approval. The CaseFlow edge-trace endpoint can remain unavailable in this narrow
composition; the editor displays canonical events without inventing trace proof.
Public status reads can reject a concurrent revision change; the loader discards
mixed snapshots and offers a fresh bounded reload.

## SOLID review

XML inspection, expression compilation, projection, admission and pure control
decisions are separate modules (SRP). Integration reuses queue, policy and
checkpoint ports (DIP/ISP); controls do no worker work. Optional fields preserve
legacy compatibility; capability checks prevent substitution by incapable
runtimes (LSP). The large existing Native orchestrator and Angular component
remain SRP debt; XML metadata handling, the pure runtime projection and runtime
loading are extracted. The runtime loader depends only on its three read
operations (ISP/DIP); the UI neither schedules work nor grants Hub authority.
The neutral workflow DTO's
call to a separate BPMN admission facade is explicit cross-domain compatibility
debt, not another orchestration layer. Loop/event additions must continue through
Hub-owned activation and durable wait contracts, never Worker orchestration.

The metadata descriptor uses the upstream [bpmn-moddle XML model](https://github.com/bpmn-io/bpmn-moddle).
