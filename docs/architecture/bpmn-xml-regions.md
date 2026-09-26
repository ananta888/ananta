# Source-bound BPMN XML regions

The Hub lowers a narrow structured XML subset through the existing path:
`import_bpmn_xml → VisualProcessGraph → WorkflowRequest → ExecutionPlan → Native`.
`bpmn_xml_regions.py` is an import transformation, not a workflow executor.
Workers execute only the Hub's individual expanded assignments.

## Source binding and limits

The graph retains exact `bpmn_source_xml` and its UTF-8 SHA-256. Admission
re-imports that source and compares the canonical expanded nodes, edges,
conditions, projections and source lineage. Request step IDs therefore already
match the expanded plan and read-model IDs. Editing a compiled projection does
not edit its source: update XML and re-import. Export of a region graph returns
the retained structured XML, not a flattened substitute.

Generated `bpr_…` IDs depend on source hash, XML element, complete scope/iteration
path and generated role. `bpmn_activation_origin` uses schema
`ananta.bpmn_xml_origin.v1`, with `source_sha256`, `source_id`, `scope`, and `role`.
The graph manifest is `bpmn_xml_regions` / `ananta.bpmn_xml_regions.v1`.
The execution graph's definition hash includes the complete source and expanded
metadata; `node_metadata` carries verified bindings. These compile identities
are not Evidence Registry `SRC_*` or `RUN_*` identifiers.

Default compiler limits are 32 body iterations, depth 8, 4096 generated nodes
and 16384 edges. Every scope must be acyclic; arbitrary sequence-flow backedges
remain rejected. Expansion checks bounds during emission. Runtime execution
inherits the admitted Hub plan budget, including its persisted whole-run
deadline. `validate_region_budget` requires an explicit finite cost ceiling and
rejects child budget increases; XML does not allocate execution authority.
Children inherit the request policy scope and may not exceed its tool allowlist.

## Explicit data projection

All tasks and gateways in a region-containing document, including outer
consumers, require Ananta metadata with an explicit projection:

```json
{
  "bpmn_input_projection": {
    "schema": "ananta.bpmn_input_projection.v1",
    "workflow_input": {"value": ["input", "value"]},
    "dependency_results": {"previous": ["results", "previous_task"]}
  }
}
```

`workflow_input` paths start at `input` or `results`; `dependency_results` paths
start at `results`. Paths contain 2–16 literal string keys and use dictionary
traversal only. Each dictionary permits at most 128 aliases. There is no
expression evaluation, wildcard expansion, array indexing or ambient fallback.
Empty maps expose no data. Missing fields fail before task delegation. Returned
values are detached bounded JSON objects.

Source `input` means the current lexical region input; `results` names only
sibling exports. Import rebinds these paths to canonical upstream node IDs.
Conditions on a scoped gateway see only its projected input and result aliases.
Internal raw results remain Hub state; outer workers receive only explicit
exports. Source references to unknown scopes or future nodes fail admission.

## Embedded subprocess

An ordinary `subProcess` must have one connected plain start and one plain end.
Its Ananta metadata must declare both maps, even if empty:

```json
{
  "bpmn_region": {
    "schema": "ananta.bpmn_region.v1",
    "input_mapping": {"value": ["input", "public_value"]},
    "output_mapping": {"value": ["results", "work", "value"]}
  }
}
```

The entry projection exports the admitted local input. Every child declares
its own projection; the exit exports only `output_mapping`. The subprocess's
source ID denotes that exit result in its parent scope. Nested scopes repeat
this composition, under the depth bound. Child failures terminate the run;
cancellation uses the existing Hub assignment-cancellation path and cannot
start later children. Restart resumes the signed expanded plan without a new
definition lookup.

Event subprocesses, boundary events, multi-instance regions, artifact mappings,
and `callActivity` remain unsupported. Embedded definitions are pinned by the
retained XML hash. No compatibility/latest call resolution is enabled.

## Standard pre-test loop

Only `standardLoopCharacteristics` on task, serviceTask, userTask or subProcess
is accepted. `testBefore` must explicitly be `true` (or `1`), `loopMaximum` an
integer from 1 through the Hub limit, and one nonempty `loopCondition` must use
`ananta-condition-v1`. The condition reads only the explicit loop-state input.

The activity metadata adds:

```json
{
  "bpmn_loop": {
    "schema": "ananta.bpmn_loop.v1",
    "input_mapping": {"value": ["input", "value"]},
    "repeat_mapping": {"value": ["results", "again", "value"]}
  },
  "bpmn_input_projection": {
    "schema": "ananta.bpmn_input_projection.v1",
    "workflow_input": {"value": ["input", "value"]},
    "dependency_results": {}
  }
}
```

Here the XML activity ID is `again`. Initial and repeat mappings declare
identical state aliases. The entry map establishes state 0; the condition is
tested before the first body and each later body. A false initial condition
executes zero bodies and exports state 0. After a successful body, repeat mapping
creates the next state from that body's result and the current scoped input.
Reaching `loopMaximum` completes normally and exports the last mapped state;
it does not perform another condition check or report a budget failure.
For a looped subprocess, its explicit output mapping supplies the body's result.

This differs from the blocked component-preview compiler described in
[BPMN activation expansion](bpmn-activation-expansion.md): its final still-true
condition is an exhaustion failure. That preview contract is not the XML
standard-loop contract and remains outside public admission.

Each possible exit produces an explicit projection of state. The common exit
uses a projection control and closed selection metadata:

```json
{
  "bpmn_control": {"kind": "projection", "outgoing": []},
  "bpmn_input_projection": {
    "schema": "ananta.bpmn_input_projection.v1",
    "workflow_input": {},
    "dependency_results": {}
  },
  "bpmn_projection_join": {
    "schema": "ananta.bpmn_projection_join.v1",
    "sources": ["expanded_return_0", "expanded_return_1"]
  }
}
```

Sources must exactly match incoming predecessors. All must be terminal and
exactly one completed rather than skipped. The result is that source's detached
object. Zero/multiple active sources, conflicting terminal states and missing
or non-object outputs fail; there is no order-based selection or merge with
ambient data. Validation and execution use `bpmn_projection_control.py`.

## Integration and verification

The ExecutionPlan adapter preserves only source-validated `node_metadata`;
projection/join nodes require `bpmn_activation_v1` in addition to ordinary
`bpmn_control_v1`. Native calls `project_control_result` after its usual DAG
readiness and route checks. The exact-source join validator and finite-region
budget validator run during plan admission. Timer/message import hooks provide
separate `execution_graph.waits` definitions for the event implementation.

`tests/bpmn/test_xml_regions.py` covers source/request tampering, mapping
confinement, nesting bounds, loops at zero/one/multiple iterations and the
maximum, exported final state, looped subprocesses, missing inputs, failure,
restart, stale results, cancellation and finite runtime budgets. Run with cached
`ananta-backend-tests:local`, `--network none`, read-only `/workspace`, SQLite in
`/tmp`, and pytest options `--confcutdir=tests/bpmn -o addopts= -p no:cacheprovider`.
These are synthetic technical observations, not deployment or production
release evidence. Successful tests do not enable unsupported BPMN elements or
remove rollout/policy gates. Strict pre-dispatch cost reservation for arbitrary
task types remains a separate runtime concern; observed over-budget results
must fail and prevent later iterations.

The XML adapter, closed mapping/rebinding contracts, graph expansion, pure
projection control and runtime orchestration retain distinct responsibilities
(SRP). The existing DAG and delegation ports remain unchanged (OCP/DIP). Native's
large orchestration service remains existing SRP debt; this implementation adds
a small pure control helper rather than another execution loop.
