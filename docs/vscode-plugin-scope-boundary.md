# VS Code Plugin Scope Boundary (VSC-T01)

## Purpose

Define a safe, thin-client runtime scope for the Ananta VS Code extension.

## MVP scope (included now)

1. Connection/profile/auth configuration with validation.
2. Backend health/capability checks.
3. Command entrypoints for explicit developer-triggered workflows.
4. Read-oriented status/task/artifact/approval visibility.
5. Explicit TUI/Web fallback launch hooks.

## Advanced scope (later)

1. Rich task/artifact drilldowns and inline previews.
2. Multi-view dashboards and advanced filtering.
3. Progressive context assistants and contextual quick actions.

## Browser-fallback / deferred scope

1. Deep admin/config workflows.
2. Complex repair execution flows.
3. Risky bulk operations.
4. Rich/binary rendering with heavy UX.

## Hard boundaries

1. The extension is a thin adapter, never a control plane.
2. Orchestration, policy, approval, audit and repair enforcement stay in backend.
3. No implicit file mutation, approval action or repair execution.
4. Risky actions require explicit user confirmation and backend permission.
5. Context payloads must be bounded, inspectable and safe.
