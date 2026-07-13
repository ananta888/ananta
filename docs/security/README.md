# Security Documentation

## Workflow runtime

- [Production workflow-runtime threat model](workflow-runtime-threat-model.md)
- [Machine-readable workflow-runtime security gate](workflow-runtime-security-gates.v1.json)
- [Approval lifecycle](approval-lifecycle.md)
- [Least-privilege policy](least-privilege-policy.md)
- [Workflow credential boundary](workflow-credential-boundary.md)

The runtime threat model is normative for Native, LangGraph and Temporal
production profiles. It links every critical threat to prevention, detection,
audit and automated evidence; open critical findings block production.

## Execution boundaries

- [Worker tool-calling policy](ananta-worker-tool-calling-policy.md)
- [Worker workspace mutation policy](ananta-worker-workspace-mutation-policy.md)
- [Shell command policy](shell-command-policy.md)
- [Terminal threat model](terminal-threat-model.md)
- [Default deny](default-deny.md)

## Operations and deployment

- [RBAC operations guide](rbac-operations-guide.md)
- [OIDC](oidc.md)
- [Sandbox operations guide](sandbox-operations-guide.md)
- [Artifact trust boundaries](artifact-trust-boundaries.md)

Runtime architecture, rollout and recovery are documented in the
[workflow-runtime architecture](../architecture/workflow-runtime.md) and
[workflow-runtime rollout runbook](../operations/workflow-runtime-rollout.md).
The production LangGraph checkpoint boundary and its read-only Compose secret
wiring are covered by the
[LangGraph Hub-owned checkpoint runbook](../operations/langgraph-hub-checkpoint-runtime.md).
