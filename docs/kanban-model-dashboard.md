# Kanban and model dashboard

This feature projects existing hub tasks and the backend model catalog into
Angular and the operator TUI. It does not introduce another task queue,
worker controller, provider registry, or model runtime.

## Rollout flags

All flags default to `false`:

| Surface | Environment variable |
|---|---|
| Angular Kanban | `ANANTA_ANGULAR_KANBAN_ENABLED` |
| Angular model dashboard | `ANANTA_ANGULAR_MODEL_DASHBOARD_ENABLED` |
| TUI Kanban | `ANANTA_TUI_KANBAN_ENABLED` |
| TUI model menu | `ANANTA_TUI_MODEL_MENU_ENABLED` |

The backend publishes the effective values through
`GET /config/features/v1`. A disabled flag removes the corresponding
navigation entry and blocks direct access. Backend API and capability checks
remain authoritative even if a client is modified.

Roll out and roll back one stage at a time:

1. Read-only models
2. Read-only Kanban
3. Kanban writes
4. Allowlisted default-model selection

Worker start and model load/unload are not part of this rollout.

## Angular

### Kanban

Open `/board` after the Angular Kanban flag is enabled. Columns and cards are
projections of existing hub task statuses and IDs.

Supported operations:

- Filter and search projected tasks.
- Open task details, comments, dependencies, and activity.
- Move a card with pointer controls or the keyboard alternative.
- Assign, comment, block, or complete through server-side commands.
- Recover from a revision conflict by loading a fresh snapshot.

Every write sends the card revision and an idempotency key. A `409` means
another actor changed the task first. The client discards the optimistic
view and reloads the authoritative snapshot.

### Models

The compact model view is part of the existing configuration/navigation
surface. It shows server-normalized provider, runtime, availability, loaded
state, context window, quantization, capabilities, health, and default state.

Only catalog refresh and selection of an available allowlisted default are
supported. The UI never accepts provider URLs, model paths, shell arguments,
or load/unload commands.

## Operator TUI

The `kanban` and `models` sections use the existing section, content-plugin,
region, mouse, focus, and renderer infrastructure.

Kanban controls:

| Input | Action |
|---|---|
| Arrow keys or mouse | Select a column or card |
| Enter or double click | Open details |
| Left/right or target-column action | Move without terminal drag-and-drop |
| Action menu | Assign, comment, block, or complete |
| Escape | Cancel or return |

Model controls:

| Input | Action |
|---|---|
| Arrow keys or mouse | Select a provider/model |
| Enter | Open details or invoke the offered safe action |
| Refresh action | Refresh the backend catalog |
| Default action | Select an available server-allowlisted model |

Mouse support is optional. All operations remain reachable by keyboard.
Small terminals use a list layout. Larger terminals may display columns.
Resize rebuilds hit regions while preserving a valid selection.

## Permissions

Read, write, assign, comment, catalog refresh, and default selection are
separate server capabilities. A hidden button is not an authorization
control. The server validates identity, tenant/team/goal scope, task ID,
revision, transition, and action capability for every request.

Unknown or foreign resources may be concealed as not found to avoid IDOR
disclosure. Authentication-disabled development mode does not authorize
catalog writes.

## Troubleshooting

### The section or route is missing

Check the matching environment flag and the effective response from
`GET /config/features/v1`. Restart the affected service after changing an
environment variable.

### A card move was reverted

Inspect the stable error code. For a revision conflict, reload the board and
repeat the action against the current card revision. For a transition error,
choose a status transition allowed by the hub task policy.

### Live updates stopped

The client reconnects with authentication, deduplicates events, detects
revision gaps, and falls back to a REST snapshot. Persistent failures should
be investigated in hub event health and authentication renewal logs. Do not
accept an unsequenced event stream as current.

### One provider is unhealthy

Provider failures are isolated. Healthy providers remain visible. Catalog
responses contain stable health information but never raw exceptions,
credentials, or provider endpoints.

### A model cannot become the default

Refresh the catalog and confirm the model is available. Only a model in the
current server-side allowlist can be selected. Direct URLs and paths cannot
be supplied as a workaround.

## Release evidence

Run `scripts/run_kanban_model_dashboard_release_gate.py` with an immutable
commit SHA and fresh evidence for contract, backend, Angular, TUI, security,
accessibility, and performance suites. Evidence older than 24 hours, bound
to another commit, malformed, missing, or symlinked fails closed.

Local UI behavior alone is not release evidence. Rollout remains disabled
until the gate for the exact deployed commit is green.
