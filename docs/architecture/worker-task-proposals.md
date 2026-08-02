# Worker task proposals

Worker task proposals let a role report newly discovered work without granting
that Worker any orchestration authority. The Hub remains the only owner of
planning artifacts, Tasks, routing, approvals and the queue.

## Trust boundary

A proposal is accepted only through the callback capability minted for one
assignment/result pair. The ingress verifies the organization, project,
tenant, Goal, Task, role slot, assignment, dispatch lease, attempt, policy
revision and payload digest. A callback capability cannot list Tasks, invoke
AutoPlanner, create generic follow-ups or submit a proposal for another lease.

The Worker may provide role and team hints. These are untrusted preferences;
it cannot force an agent address, queue, priority, credential or execution
backend. Unknown fields are rejected by the closed schema in
`schemas/worker/task_followup_proposal.v1.json`.

The Hub derives the set of known role, team and opaque assignment references
from the persisted organization topology at materialization time. Track text
cannot extend that set, and Worker URLs are not exposed as proposal target
identifiers. The role policy's `target_scope` bounds candidate units before
capability, capacity, risk and separation-of-duties evaluation.

## Hub decision flow

```text
delegated assignment
  -> Worker result with bounded proposal carrier
  -> capability and lease verification
  -> schema, scope, grounding, depth, budget and policy validation
  -> Track-amendment classification
  -> Hub candidate routing and separation-of-duties check
  -> exact-revision approval when required
  -> Planning Track amendment
  -> idempotent Hub Task materialization
  -> locked final agent/assignment selection at execute-next
```

Validation and decision persistence are separate. The proposal policy defaults
to deny; role and slot policies may only narrow the capabilities of the source
assignment. Replay of the same proposal/idempotency key returns the previous
decision. Retry with changed content under the same key is rejected. Recursion
depth, proposal count, work estimate, token/cost/time budgets and amendment
count are bounded.

An accepted amendment stores the Hub-selected unit, team and role slot in its
`organization_binding`. A classification-time agent/assignment selection is
recorded only as an auditable preview. Capacity and lifecycle may change, so
execute-next re-reads the topology and persists the final agent and assignment
with the durable dispatch intent. No Worker hint participates in ranking.

## Grounding

Schema shape does not establish evidence. Each claim reference must be an
exact member of the source/run allowlist supplied for the current assignment.
Missing, unknown, orphaned or scope-foreign identifiers remain unverified and
cannot justify promotion, adoption, a handoff or a gate.

## Outcomes

- `rejected`: fail-closed policy, scope, lease, evidence or budget result.
- `needs_approval`: valid proposal awaiting an exact revision/digest decision.
- `accepted_as_plan_amendment`: Hub classified and persisted the amendment;
  Task creation still follows the guarded materialization transition.
- `superseded`: a newer Hub decision or plan revision replaced the proposal.

Every outcome records stable reason codes and policy hashes. Audit records are
redacted and contain no reusable callback credential or direct Worker secret.
