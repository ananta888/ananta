# Enterprise organizations operator runbook

This runbook covers safe operation of multi-team organizations. Every command
targets the Hub API. Never call a Worker directly and never treat a Worker
database as control-plane state.

## Preconditions

- Authenticate to the Hub with the least-privileged principal.
- Select the trusted tenant/project scope through the authenticated context;
  do not send scope ownership in a request body.
- Use a short-lived, plan-bound precreation grant for instantiation. The Hub
  consumes it atomically and returns a separate revocable, organization-bound
  grant for later mutations.
- Keep the definition revision, policy revision/hash and dry-run digest from
  the response. Do not recompute or edit them client-side.

TUI discovery commands are read-only:

```text
:org status
:org blueprints
:org list
:org show <organization-id>
:org show <organization-id> --mermaid
:org planning <organization-id>
:org proposals <organization-id>
```

The TUI obtains write authority only from
`ANANTA_ORGANIZATION_ADMIN_GRANT`; a grant must not be placed in shell history
as a command argument.

## Compile and instantiate

1. List production Organization Blueprints.
2. Compile the selected definition with a team count. Eight is the default;
   five through ten are standard. Two-/three-team test fixtures are not listed.
3. Confirm exact team/unit/relation/slot counts, capability gaps, budgets,
   blockers, effective limits and planned writes.
4. Treat the returned plan digest, definition revision, limit-profile revision
   and hash as one inseparable receipt.
5. Request a short-lived Instantiation Grant for that exact receipt.
6. Confirm deliberately, then apply with a stable operation idempotency key.

In Angular, the Hub-issued Instantiation Grant is requested automatically when
the operator proceeds from the Dry-run to confirmation. It is held only in
memory, never rendered as a secret input and invalidated when the plan, project
or Hub changes. "Bewusst instanziieren" is the human confirmation boundary for
the control-plane writes; it does not activate the organization and does not
start Workers or Tasks. On success the Hub returns a different, revocable
Organization Admin Grant for later management operations.

While the final write is in flight, Angular blocks project changes and route
navigation. If a timeout or another ambiguous transport failure occurs, that
lock, the exact plan and both idempotency bindings remain in memory. Use
"Ergebnis sicher abrufen" to replay the identical request; do not navigate away
or compile a replacement plan until the Hub returns a definitive result.

```text
:org validate enterprise_scrum_organization --teams 8
:org instantiate --confirm
```

### Custom N with one-shot admission

Custom N is not a hidden team-count override. First request a scoped exception,
then compile the exact same counts. Angular performs these two Hub calls in
sequence. The TUI equivalent is:

```text
:org validate enterprise_scrum_organization \
  --composition enterprise_product_delivery_scrum=2,portfolio_product_coordination=1,platform_devops_sre=1 \
  --reason "Bewusst reduzierte Startkomposition"
:org instantiate --confirm
```

The admission endpoint requires project `MANAGE` plus a fresh
`Idempotency-Key`. It accepts `blueprint_version`, positive
`team_blueprint_counts`, a non-empty reason of at most 512 characters and a TTL
from 60 to 3600 seconds (default 900). Record the returned definition revision,
composition digest, policy hash, capability gaps and expiry together. The
exception reference is an authorization capability: do not log it or place it
in shell arguments. Angular keeps it only inside the in-memory signed compile
plan; the TUI redacts the value and reports only that one was issued.

Before confirming Instantiate, verify that:

- every count matches the issued receipt exactly;
- the principal and target project are unchanged;
- definition revision and limit-policy hash are current;
- the exception and the separate plan-bound precreation admin grant are both
  unexpired; and
- reported capability gaps are consciously accepted by governance.

Compile only validates the exception; it does not consume it. Instantiate
recompiles server-side and consumes the exception, precreation grant and
aggregate write in one transaction. If the transaction fails, both grants and
all rows roll back. After success, never retry with a new Instantiate
idempotency key: the exception is one-shot. Retry only the identical successful
payload with its original key to retrieve the existing result.

Apply fails before the first write when the plan expired, its scope/principal
changed, definition or limit policy is stale, a blocker appeared, or a custom
admission exception is missing/consumed. Retry the identical payload with the
same idempotency key. For a changed payload, compile a new plan and use a new
key.

Instantiation creates definitions, planned units, team links, inactive teams,
planned role slots, relations and snapshots atomically. A later Organization
lifecycle transition to `active` promotes that materialized topology in the
same transaction. Agent assignments remain a separate Hub-governed operation;
neither step starts Workers or Tasks.
The Angular client keeps the returned organization grant in memory only and
clears it when the Hub changes. Other clients must transfer it directly into a
secret store; never print it, persist it in layout state or pass it as a command
argument. The TUI deliberately redacts the value from command output.

## Scale and topology management

Use the draft-patch preview for add/remove/reparent/connect/assign operations.
Review capability, lifecycle, separation-of-duties, budget, limit and lineage
diagnostics. Runtime relations cannot be patched.

For active teams choose an explicit strategy:

- `drain`: atomically pause affected work and release its current jobs/leases;
- `migrate`: move work to an explicitly selected successor
  Organization/Unit/Team/Role-Slot while preserving `source_task_id` lineage;
- `archive`: only for idle work; preserve lineage and remove the team from
  active routing.

Apply the exact preview with `If-Match`, patch digest, policy revision/hash,
idempotency key and grant. A stale response is not repairable client-side;
preview again. Resize uses the same cardinality-driven compiler for 2→N→2
and never deletes a seed or runtime record blindly.

Layout changes use the presentation-only layout endpoint. They may be retried
without changing definition or runtime revisions.

## Assignment and proposals

Before assigning an agent to a Role Slot, inspect required capabilities,
capacity, multi-team limits and separation-of-duties conflicts. Assignment
uses the same policy resolver as Hub routing.

### Publish a planning Source Catalog

The first planning phase needs a Hub-owned, Organization-scoped Source
Catalog. Its input is retrieval intent, not evidence identity:

```http
POST /api/organizations/<organization-id>/source-catalogs
Authorization: Bearer <operator token>
Idempotency-Key: <stable-publish-key>
Content-Type: application/json

{
  "connection_id": "<active-source-connection>",
  "queries": ["HRM architecture", "training safety and evaluation"],
  "limit": 20
}
```

The caller must not submit source IDs, URLs, repository revisions, manifests,
hashes, tenant scope or an allowlist. The Hub queries only the connection's
active admitted index, revalidates its exact revision/run/manifest lineage,
assigns deterministic `SRC_*` identities and commits one completed,
content-free Catalog Task with its operation and audit receipt. Queries and
retrieved content are not persisted in that Task; only bounded selectors and
digests are stored. An unchanged retry with the same key returns the same
Catalog, while changed input or a changed active index fails closed.

Keep the returned Catalog Task ID for readiness. At Category-research start,
the Hub locks that exact Task, revalidates the active source/index lineage and
reads only its bound records. Every reconstructed record must match its
cataloged content hash. Retrieved content is then written only into the new
research Task's `ContextBundle`; it is never copied into the Catalog Task,
operation, audit event, retrieval-run metadata or Worker routing fields. A
stale index, modified record or partial context write rolls back the complete
research-Task transaction.

### Category-research readiness and start

Before starting the first Organization planning phase, call the read-only
readiness endpoint with only the target identifiers and the persisted Source
Catalog Task selector:

```text
GET /api/organizations/<organization-id>/goals/<goal-id>/planning/category-research/readiness
    ?unit_id=<unit-id>&team_id=<team-id>&role_slot_id=<slot-id>&catalog_task_id=<catalog-task-id>
```

The Hub checks the scoped Organization Goal, active Organization/Team/Role
Slot, and at least one active eligible assignment. Eligibility is the union of
the Role-Slot policy and the mandatory `research`, `planning` and
`source_analysis` capabilities, including current registration, availability
and capacity. The response never contains an Agent URL. It is explicitly
read-only and reports `task_write=false` and `queue_write=false`.

The browser must not construct hashes, revisions, scopes, source IDs or an
allowlist. The Hub derives the complete `source_catalog_binding` from the
selected persisted Catalog Task and re-runs the normal source authority checks.
Every released source reference must carry the exact
`organization:<organization-id>` scope; repository-global or foreign
Organization scopes are not valid for this planning assignment.
Pass that returned binding unchanged to:

```text
POST /api/organizations/<organization-id>/goals/<goal-id>/planning/category-research
Idempotency-Key: <fresh-operation-key>
```

Readiness is a snapshot, not a reservation. Immediately before inserting the
research Task, the Hub locks where supported and revalidates Organization,
non-terminal Goal, Team, Role Slot, current Source Catalog, active assignment,
required capabilities and capacity inside the Task transaction. A lifecycle,
Catalog, capability or capacity change fails closed without a Task or queue
write. Retry readiness after fixing the reported blocker; never edit its
server-resolved binding client-side.

The created Task persists the Hub-selected Assignment and Agent binding; an
Organization Task never falls back to global Worker selection. Immediately
before forwarding, the Hub revalidates the Organization lifecycle, topology,
Assignment, Agent registration, capability set, capacity and WorkerJob lease.
The destination Worker accepts the payload only through its service-only
local intake after verifying the payload-bound, short-lived Hub capability
against its public Ed25519 verification keyring and recomputing the
ContextBundle digest. The Worker service token authenticates only the request
transport; only the Hub holds private signing material. Workers execute this
exact delegation but never select or contact another Worker. A Worker without
a canonical configured `AGENT_URL` or public verification keyring rejects the
intake as unavailable.

Proposal triage:

1. Open `:org proposals <organization-id>` or the Planning panel.
2. Verify source Task, assignment/lease binding, role slot, proposal revision
   and digest, policy hash, budget and Hub-selected destination.
3. Reject a stale, recursive, scope-mismatched or privilege-escalating
   proposal.
4. Approve the exact digest only when it is a valid Track amendment.
5. Confirm the Hub created one amendment/mapping and routed the resulting Task;
   never create the Task manually as a workaround.

Role/team/agent fields sent by a Worker are hints. The Hub is authoritative.

## Organization Goal intake

Create a passive Organization Goal before starting Category research. The
authenticated Organization administrator needs project `MANAGE`, an active
`organization_admin` or `planning:goal_create` grant and a fresh
`Idempotency-Key`. The request accepts only `goal`, `summary`, `constraints`
and `acceptance_criteria`; scope, lifecycle, creator and Goal kind are always
set by the Hub.

```http
POST /api/organizations/<organization-id>/goals
Authorization: Bearer <operator token>
Idempotency-Key: <stable retry key>
Content-Type: application/json

{
  "goal": "Produce a grounded HRM Category-Todo",
  "summary": "Research the HRM experiment workbench",
  "constraints": ["Use only assignment-bound SRC_*/RUN_* references"],
  "acceptance_criteria": ["The result conforms to todos/todo.schema.json"]
}
```

An unchanged retry returns the original Goal. Reusing the key for different
content fails closed. Goal intake starts no Worker and creates no Task; use
the separate Category-research endpoint only after the source catalog and the
Organization research-role binding are authoritative.

## Pause, archive and recovery

Pause atomically marks the routing topology as draining, suspends active Role
assignments with an `organization_pause` provenance marker and blocks every
new Organization Task claim/Autopilot dispatch; queued and in-flight Tasks
keep their identity and lineage. Resume reactivates that topology and only the
assignments suspended by this lifecycle operation; assignments that were
already inactive stay inactive. Completion and archive re-read all
non-terminal Tasks, including queued Tasks, under the lifecycle transaction
and require the declared drain/migrate/cancel strategy before closing them.
Before archive,
resolve or explicitly drain active Tasks, leases, gates and handoffs. Archive
retains all snapshots, mappings, dependencies, assignments, artifacts and
audit events.

Recovery restores administrative visibility but does not rerun work. Validate
the current definition/policy again and request a fresh activation decision.
Generic Team delete returns `409` for an Organization-linked team before any
member, Task or Goal association is cleared. Use the organization lifecycle
operation named in that response.

## Reconcile, upgrades and drift

Create or upgrade a project definition through an immutable revision:

1. validate the complete next definition with a new idempotency key;
2. record its parent revision, mutation digest, policy hash and one-shot grant
   together;
3. use `If-Match: none` for a new key or the exact parent revision for an
   upgrade; and
4. apply through `PATCH /api/organization-blueprints/<key>` (or the additive
   `/revisions` alias). Never edit an existing revision in place.

Preview seed reconcile with
`POST /api/organization-blueprints/<key>/reconcile-preview` and
`{"current_version": 2, "source": "seed", "local_override_paths": []}`.
Use `source: payload` plus `desired_definition` only for an explicitly reviewed
project definition. Inspect changes grouped by units, group cardinality, role
slots, workflows, relations, policies and referenced versions. Also inspect
every `assignment_impact`; it identifies a linkage that the new definition can
affect but does not move or stop that assignment. Existing instance snapshots
remain immutable and are listed under `preserved_snapshot_revisions`.

Local overrides are either preserved or reported as explicit conflicts;
removed seed definitions are never blindly deleted. A preview with
`requires_apply=false` is a no-op and deliberately has no mutation grant.
Apply only the exact complete preview to the separate `reconcile-apply`
endpoint with its one-shot grant, `If-Match` current revision and a new
idempotency key. Repeating the identical successful apply is idempotent. If the
catalog, current definition, assignments or policy changes between preview and
apply, obtain a fresh plan.

Archive is also preview-first. Resolve every reported non-archived instance
before applying the retirement marker. Standard definitions remain in the
checked-in file catalog; a scoped retired marker only hides that exact version
inside the selected project. Archive and reconcile never rewrite instance
snapshots or start Workers.

## Bundle transport

```text
:org export <organization-id>
```

Default export contains only the exact versioned definition graph. It omits
the source tenant/project/organization IDs, all local database IDs, compiled
plans, runtime snapshots, assignments, secrets and agent URLs. Requests for
`include_instances=true` add only a target-recompile recipe, never the source
runtime snapshot. `include_assignments=true` additionally requires that recipe
and exports pseudonymized principal refs without labels or Agent URLs. Neither
form is itself a portable target binding. Download filenames use the portable
root definition reference and do not repeat the source Organization ID.

For definition import:

1. enforce the configured byte limit before parsing;
2. parse and schema-validate Bundle v2;
3. request a server preview;
4. review grouped diffs, redactions and conflicts;
5. ask the Hub to recompute the complete preview and issue a plan-/principal-/
   policy-bound one-shot grant;
6. apply the unchanged plan with its digest/revisions/grant/idempotency key;
7. verify the imported definition hashes.

Optional instance transfer is an explicit target-recompile operation:

1. review each portable instance key, root `key@version`, composition and
   requested draft/validated lifecycle;
2. for every pseudonymized principal ref, choose an eligible Agent URL that is
   registered in the authenticated target environment;
3. for custom compositions, bind a target-scoped, one-shot admission exception;
4. run preview; the Hub recompiles the recipe using current target definitions
   and limit policy and allocates deterministic target IDs;
5. review grouped instance/assignment diffs and obtain the recomputed grant;
6. apply definitions, instances, teams, slots, relations, eligible assignments
   and audit outbox through one transaction.

Never copy source `organization_id`, definition hashes, policy hashes,
`topology_snapshot`, compiled plans or raw Agent URLs into a recipe. Preview
reports `optional_target_recompile` and rejects those legacy fields before a
write plan can become applicable.

Use split import to deploy reusable templates/team definitions before the
organization definition. Bundle v1 remains limited to its legacy Team
Blueprint semantics and never creates an inferred Organization Instance.

## Audit and incidents

Audit records may contain principal IDs, object IDs, revisions, hashes,
reason codes and transitions. They must not contain prompt/template bodies,
credentials or exported secret values. Relevant events include compile/apply,
routing decision, handoff transition, gate decision, proposal decision,
budget reservation, lifecycle transition and reconcile result.

When execution stalls:

1. inspect Organization status, blocked DAG nodes, handoffs, gates and budget;
2. compare current definition/policy hashes with the dispatch receipt;
3. replay Hub events/read models idempotently;
4. revoke a stale assignment/lease through the Hub;
5. use bounded retry/rework or escalate to a human gate;
6. do not bypass the queue or address another Worker.

## Verification boundary

The single complete acceptance scenario is the medium eight-team reference.
Small two-/three-team matrices use repository/Hub/Worker fakes without a
browser or real Workers. Performance tests use synthetic minimal graphs and
must record hardware, data size, warmup, samples and bottleneck. A release
report may claim a gate only after its command actually ran; absent source/run
allowlists remain unverified and no evidence identifier may be invented.

### Performance evidence contract

Run the synthetic projection profile only as part of the explicitly approved
complex gate:

```text
python -m pytest -q tests/performance/test_n_team_organization_projection.py
```

The profile performs five warmups and thirty measured samples per scenario.
It records OS/platform, architecture, processor string, logical CPU count,
Python version, data size, p50/p95, budget and the suspected bottleneck in its
temporary test report. The release environment must preserve those reports
with the gate evidence; results from undocumented hardware are not accepted.

The acceptance budgets are:

- a page of 100 projected nodes: at most 12 storage queries and API p95 at
  most 500 ms on the documented deployment reference;
- a hierarchy with 32 teams: projection/UI p95 at most 1500 ms;
- a bounded graph with 500 nodes and 2000 total edges: projection/UI p95 at
  most 2500 ms.

The pure projection test verifies one batched read-port call. The release
integration profile must additionally count real SQL statements because a
fake read port cannot prove the database query budget. Cluster, pagination or
truncation must activate above the declared render limits.

### Global convergence gate

Prepare a truthful, non-executing report while implementation is in progress:

```text
python scripts/run_enterprise_organization_release_gate.py
```

Cheap static suites may be selected with `--execute-static`. The complete
backend, security, Angular, accessibility, performance and sole eight-team
Playwright flow requires `--execute-full` plus the approval environment switch
named in the checked-in release profile. Without both, the report remains
`deferred`; skipped suites can never produce a passing gate.
