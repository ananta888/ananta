# Workflow Backend Interface

Ananta starts workflows through a hub-owned `WorkflowBackend` port. The port is
intentionally neutral: routes, planning code, visual-process mapping, and
blueprint persistence do not import Temporal or any other durable-runtime type.

## Contracts

`WorkflowRequest` is the executable handoff contract:

- `schema`: `ananta.workflow_request.v1`
- `workflow_id`, `workflow_type`, `goal_id`, `plan_id`, `blueprint_id`
- ordered `steps`
- explicit `policy_scope`
- optional `allowed_tools`, `input_artifacts`, `metadata`
- `correlation_id` for audit and idempotency

Each step carries:

- `step_id`, `title`, `task_kind`, `role`
- `depends_on`
- `gate`
- `policy_scope`
- `allowed_tools`
- input and output artifact names

The request validator rejects duplicate step IDs, unknown dependencies, missing
steps, and missing effective policy scope.

## Backends

The default backend is `local`. It validates the request, records an in-memory
run, models approval/cancel signals, and returns
`ananta.workflow_backend_status.v1`.

The optional `temporal` backend is selected with:

```bash
ANANTA_ORCHESTRATION_BACKEND=temporal
ANANTA_TEMPORAL_ADDRESS=localhost:7233
ANANTA_TEMPORAL_NAMESPACE=default
ANANTA_TEMPORAL_TASK_QUEUE=ananta-workflows
ANANTA_TEMPORAL_WORKFLOW_TYPE=AnantaWorkflow
```

If the Temporal Python SDK or server is not available, the adapter returns a
degraded status instead of failing imports in the hub. If both are available,
the adapter starts the configured workflow type with the serialized
`WorkflowRequest` payload.

## Architecture Boundary

The hub remains the control plane. A backend receives a validated request and
reports status/events; it does not decide policy, grant tool privileges, or let
workers orchestrate other workers.

This keeps SRP and DIP intact:

- routes expose API behavior only
- visual-process code maps design contracts
- backend adapters implement execution handoff
- workers still execute only delegated work
