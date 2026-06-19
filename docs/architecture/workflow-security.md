# Workflow Security Boundary

Workflow diagrams are design input, not authority.

## Default Rules

- BPMN metadata cannot grant file, shell, network, model, or secret access.
- Tool access must be present in the hub-validated `WorkflowRequest`.
- Missing policy scope rejects the request.
- Workers execute delegated tasks only.
- Worker-to-worker orchestration remains disallowed.
- Workflow events are audit data and must not contain raw secret payloads.

## Review Checklist

Before adding new workflow capabilities, verify:

- the hub still owns routing, policy, task queue, and workflow start
- the backend adapter depends on the neutral port, not route-specific state
- the UI only calls hub APIs
- Temporal activities, if enabled, delegate execution through hub-approved
  worker/task contracts
- status APIs remain compatible with existing workflow status readers

This preserves SRP by keeping diagram editing, mapping, policy validation, and
execution handoff in separate modules. It preserves DIP by depending on the
`WorkflowBackend` abstraction rather than concrete backends.
