# ADR: Hub-task Kanban and model-catalog projections

- Status: Accepted
- Date: 2026-07-23
- Track: `hermes-inspired-kanban-model-dashboard-angular-tui`
- Baseline commit: `375499a59d5796192a137e13b39c11a30b1829d2`

## Reproducible baseline

| Input | SHA-256 |
|---|---|
| `requirements.txt` | `f6bee3e1c5476c6196ac8ef339c3826710f6f753955a219ce22fb385fd6677f2` |
| `requirements-dev.txt` | `258da292858d20bb94b0394761c20fd4e4120bd76bced5782c04cc84aee8345b` |
| `frontend-angular/package-lock.json` | `653e8fe7341531cbdbefcec2c5494b9ff0d93728ea0a829a7da6d761138c05f5` |

The relevant existing extension points are:

- Hub tasks and planning projection:
  `agent/services/planning_track_task_integration_service.py`,
  `agent/routes/tasks/management.py`, and the existing task repositories.
- Provider catalog:
  `agent/routes/config/providers.py`.
- Angular:
  `frontend-angular/src/app/features/tasks/task.routes.ts`,
  `frontend-angular/src/app/components/board.component.ts`, and
  `frontend-angular/src/app/features/tasks/task-management.facade.ts`.
- Operator TUI:
  `client_surfaces/operator_tui/sections.py`,
  `client_surfaces/operator_tui/adapters.py`,
  `client_surfaces/operator_tui/plugins.py`,
  `client_surfaces/operator_tui/region_index.py`, and
  `client_surfaces/operator_tui/mouse_event_handler.py`.

## Decision

### Hub tasks remain authoritative

The hub task system remains the only source of truth for task identity,
status, revision, dependencies, queue ownership, and execution. A Kanban
card is a versioned projection of a hub task. A column is a presentation of
an existing task status. No `Board`, `Card`, or `Column` persistence model
may be introduced.

The additive API is rooted at `/api/v1/kanban`. Read endpoints expose virtual
boards and card projections. Write endpoints accept explicit
`expected_revision` and `idempotency_key` values and delegate to hub-side
task commands. UI code never mutates canonical task state directly.

Moving, assigning, commenting, blocking, or completing a card does not start
a worker. Worker selection, queueing, and execution continue to use the
existing hub-owned task flow.

### The backend model catalog remains authoritative

Provider discovery is extracted behind a small backend catalog port. Angular
and TUI consume `ModelSummary.v1` values through `/models/catalog/v1`.
`ModelSummary.v1` contains provider and model identity, display name,
runtime, availability, loaded state, context window, quantization,
capabilities, health, and default state.

Catalog DTOs never contain credentials, provider URLs, filesystem paths, or
shell arguments. Refresh and default selection are the only write actions in
this track. Default selection is restricted to a model in the server's
current catalog allowlist. Model load and unload are out of scope.

### Delivery adapters stay small

- Flask routes perform authentication, request parsing, and response mapping.
  Domain decisions remain in injected services.
- Angular uses focused typed Kanban and model-catalog clients plus stores.
  `TaskManagementFacade` is not expanded with the new responsibilities.
- The TUI uses `SectionAdapterRegistry`, `ContentPlugin`, and injected async
  backend ports. Renderers receive serializable view state and contain no
  provider or task-domain logic.
- `ShellPlugin` is not a Kanban or model action adapter.

This separation protects SRP, ISP, and DIP while keeping current APIs
backward compatible.

### Feature and rollout boundary

The following independent flags default to false:

- `ANANTA_ANGULAR_KANBAN_ENABLED`
- `ANANTA_ANGULAR_MODEL_DASHBOARD_ENABLED`
- `ANANTA_TUI_KANBAN_ENABLED`
- `ANANTA_TUI_MODEL_MENU_ENABLED`

Disabled backend projections, routes, navigation entries, and TUI sections
fail closed. Rollout order is read-only models, read-only Kanban, Kanban
writes, and allowlisted default selection. Worker start and model
load/unload are never rollout stages for this track.

## Trust boundaries and threats

| Boundary | Primary threats | Required controls |
|---|---|---|
| Browser to hub | IDOR, CSRF, XSS, stale revision, replay | Existing authentication, explicit capabilities, sanitization, revision and idempotency checks |
| TUI to hub | forged IDs, stale revision, direct provider or shell input | Typed backend ports, server authorization, no direct URLs or shell adapter |
| Hub to persistence | lost updates, partial ordering changes, dependency cycles | Atomic commands, optimistic locking, deterministic ordering and cycle checks |
| Hub to providers | SSRF, credential disclosure, expensive action abuse | Server-owned adapters and allowlist, redacted DTOs, rate limits |
| Hub to workers | capability escalation, implicit execution | Existing hub queue and policy checks; Kanban commands cannot start workers |
| Live-event channel | unauthenticated subscription, replay, gaps, storms | Authenticated connect and renewal, sequence/revision checks, deduplication, bounded backpressure and REST recovery |

Negative tests must cover foreign tenant and task IDs, stale revisions,
invalid transitions, dependency cycles, HTML and URL payloads, WebSocket
authentication and replay, provider SSRF, command injection, and configured
rate limits.

## Hermes inspiration and provenance

Only the generic interaction concepts of a column-oriented task view,
compact cards, filtering, and keyboard/mouse parity are accepted as
inspiration. No Hermes source code, assets, text, trade dress, or undocumented
API behavior is imported.

No external Hermes artifact has been supplied with an approved source
identifier, license text, or content digest. Such material therefore remains
unverified and prohibited. Any future reuse requires a separately reviewed
source identifier, immutable digest, compatible license, attribution record,
and explicit provenance evidence before it enters the repository.

## Consequences

- Existing hub task and provider APIs remain compatible.
- Presentation metadata may be added only when a demonstrated projection
  requirement cannot be represented by canonical task data.
- Angular and TUI share contract fixtures, revisions, transitions, and error
  codes.
- Local UI success is not release evidence. The release gate requires fresh,
  commit-bound contract, backend, Angular, TUI, security, accessibility, and
  performance results and fails closed for missing inputs.
