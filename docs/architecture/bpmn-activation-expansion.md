# BPMN activation expansion seam (BPMNX-008 / BPMNX-011)

There are two deliberately separate admission states. The canonical XML import
path now compiles the narrow, source-bound subset described in
[BPMN XML regions](bpmn-xml-regions.md): bounded pre-test loops and explicitly
mapped embedded subprocesses. It produces the existing VisualProcessGraph,
WorkflowRequest and ExecutionPlan contracts; there is no additional executor.

The component-based `BpmnActivationCompiler` described below remains a blocked
preview building block. Its candidate plans and pinned-call tests do not enable
public callActivity execution or complete either TODO. No production rollout or
release-evidence claim follows from either set of synthetic tests.

## Blocked component-preview integration

Use `BpmnActivationCompiler(definitions, limits=ActivationLimits(...)).compile(
pin, tenant_id=..., policy_version=...)` **before** admission, ownership
reservation, signing, or start. `definitions` implements the existing
`WorkflowComponentRegistry.resolve(id, version)` port. Compilation verifies
the returned ID and exact semantic version; compatibility fallback is refused.
`DefinitionPin` binds `component_id`, `version`, and `sha256` of the complete
`WorkflowComponent.to_dict()` (via `component_sha256`), including its interface,
plan, budgets and nested pins. It never resolves `latest`.

The parent compiler must first validate immutable source XML and translate only
a recognized, structured region into this closed intermediate representation.
This module does not recognize XML or infer which back edge may be removed.
Source admission, tenant/project authorization, and evidence issuance remain
the parent's Hub responsibilities. Component pins and `bpa_...` node IDs are
definition/activation identities, never `SRC_*` or `RUN_*` evidence.

The result is an `ActivationExpansion`. Its `candidate_plan` is a normal,
wire-normalized `ExecutionPlan` for review and synthetic tests. It contains no
component/activation placeholders. Its hash is the **expanded** admission hash;
do not sign the source component plan and rely on late Native expansion. Persist
the expanded plan and source binding together. Resume from that snapshot without
registry lookup. A compile uses private snapshots, so later registry mutation
cannot alter its output; a fresh compile detects hash drift.

`require_executable_plan()` deliberately raises while integration blockers
remain. Access to a candidate is not production admission. Do not clear blockers
or wire candidate plans into public start paths merely because validation or a
synthetic runtime test passes. Parent-owned runtime changes must be confirmed
and jointly tested before adding a production adapter here.

## Closed intermediate representation

A region placeholder is an `ExecutionNode(node_type="bpmn_activation")`. Its
metadata contains only `bpmn_activation`. Normal node capabilities, tools,
side-effect class, and budget are the Hub-approved ceiling for its children;
the placeholder itself is never delegated. Component plans must be acyclic,
single-entry/single-exit DAGs. Source node/flow IDs must be explicit and unique.

Subprocess metadata:

```json
{
  "bpmn_activation": {
    "kind": "subprocess",
    "body": {"component_id": "child", "version": "1.0.0", "sha256": "<64 lowercase hex>"}
  }
}
```

This version supplies structural expansion for stateless embedded regions or
exactly pinned calls. It rejects local-variable declarations, input/output
mapping fields (even empty ones), artifacts and custom component I/O schemas.
Event subprocesses, boundary gates, generic component placeholders, arbitrary
cycles and recursive calls are rejected. Subprocess local scope is not inferred
from namespaced node IDs.

Bounded pre-test loop metadata:

```json
{
  "bpmn_activation": {
    "kind": "while",
    "body": {"component_id": "body", "version": "1.0.0", "sha256": "<64 lowercase hex>"},
    "max_iterations": 3,
    "entry_condition": {"op": "eq", "field": "input.enter", "value": true},
    "repeat_condition": {"op": "eq", "field": "results.work.again", "value": true},
    "body_flow_id": "enter_body",
    "back_flow_id": "repeat_body",
    "exit_flow_id": "leave_loop"
  }
}
```

`entry_condition` may read workflow input. `repeat_condition` may read workflow
input and results in the just-completed body activation. The parent must prove
the referenced results exist on every path reaching the check. Missing results
fail closed; no stale previous-iteration fallback exists. A placeholder's result
cannot be referenced as though it were a task output. Expressions use the
existing closed condition DSL; there is no script evaluation.

In this component preview, each iteration is a separate DAG fragment. Existing Hub exclusive controls
select the body or exit. The final check permits only the exit: a still-true
condition produces `bpmn_gateway_no_matching_flow`, never successful truncation.
That control's origin has `limit_check=true` for element-linked diagnostics.
This is intentionally different from source XML `loopMaximum`: in the admitted
XML subset the maximum is a normal completion bound, with the final explicitly
mapped state exported at the limit. See the XML contract for entry/repeat and
zero-iteration semantics; do not reuse preview exhaustion semantics for XML.
A zero maximum permits only an initially false condition. Even an unreachable
body is validated. Source back edges are represented by edges to the next
iteration's check and remain traceable in the manifest.

`bpmn_activation_origin` binds every emitted node to its source definition, local
source ID and complete call/iteration path. The plan manifest similarly binds
every emitted edge. Hash-derived IDs use field-path-safe characters. Conditions,
gateway targets, selected-edge values, gates and flow IDs are rebound together.
Repeated copies have separate assignments and gates, while a restart retains
the same node IDs and uses the existing ownership/fencing contract.

## Component-preview bounds and remaining integration blockers

Hub limits bound iterations, nesting, emitted nodes and edges. Default limits
are 32 / 8 / 4096 / 16384, with hard ceilings of 256 / 16 / 16384 / 65536. Root
depth is zero. Expansion stops at the first exceeded budget. Input DAG size and
condition depth/size are also checked. Child plans and nodes may not broaden
capabilities, tools, side effects, attempts, timeout, token or cost ceilings.
Timeout and cost limits must be finite; a missing cost limit is rejected.

The integration blockers are:

- Request/read-model projection: canonical public status binds
  `binding.request.steps` IDs exactly. Expanded node IDs therefore require an
  explicit request and read-model contract for source steps, call instances and
  iterations. The candidate must not simply replace the plan in public start.
- Whole-run deadline binding: per-node signed timeouts do not by themselves give a
  persisted run deadline across restart, queue delay and approval waits. The
  parent has implemented this Native port; this extension does not yet bind or
  verify it with the expanded request. Its presence alone does not clear admission.
- Subprocess input confinement: Native's original `_node_input` supplies ambient
  workflow input and all prior results. Namespace rewriting alone cannot isolate
  subprocess data. Integration needs a closed projection port; this
  extension does not yet produce or rely on that contract.

Native already provides assignments, result fencing, checkpoints, cancellation,
gate policy and run cost accumulation. The expansion reuses them. Synthetic
tests cover early exit, limit failure, stale results, restart within an iteration,
and parent cancellation while a child is running. They do not establish a
durable wall-clock deadline or strict pre-dispatch cost reservation for every
possible delegated task type.

## Implemented projection port

The former `bpmn_activation_io` proposal is superseded by the closed
`bpmn_input_projection` contract in [BPMN XML regions](bpmn-xml-regions.md).
It is implemented by `agent/services/bpmn_input_projection.py`; the pure Hub
projection/join control lives in `agent/services/bpmn_projection_control.py`.
XML import produces these bindings and canonical request admission rechecks
them from the retained source. The component preview does not produce them and
must retain its blockers until it has its own complete source/request binding.

## Validation and SOLID boundaries

Run the focused tests using the cached image, no network or Docker socket, and
a read-only repository:

```bash
docker run --rm --pull never --network none --entrypoint python \
  -v /home/krusty/ananta:/workspace:ro -w /workspace \
  -e PYTHONPATH=/workspace -e PYTHONDONTWRITEBYTECODE=1 \
  -e DATABASE_URL=sqlite:////tmp/bpmn-activation-tests.db \
  ananta-backend-tests:local -m pytest --confcutdir=tests/bpmn \
  -o addopts= -p no:cacheprovider -q --tb=short \
  tests/bpmn/test_activation_compilation.py tests/bpmn/test_activation_runtime.py
```

These are synthetic technical observations, not Hub-registered release evidence
or separate-container deployment acceptance. No live deployment is performed.

Contracts, condition rebinding and graph expansion have separate responsibilities
(SRP). The resolver is a small injected compile-time port (DIP/ISP), satisfied by
the existing registry without altering its compatibility semantics (LSP).
Expansion adds new modules and existing DAG nodes (OCP); there is no task loop,
worker-to-worker orchestration, global registry or hidden I/O. Existing Native
orchestrator size and its broad input assembly remain SRP/ISP debt owned by the
parent; a focused projection adapter is the intended seam.
