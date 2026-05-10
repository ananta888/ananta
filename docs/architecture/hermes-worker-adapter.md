# Hermes Worker Adapter (Governed External Worker)

## Purpose

This document defines how Hermes is integrated as an external worker adapter in Ananta without changing the hub-owned control plane.

## Ownership Boundary

Ananta Hub remains the owner of:
- goals
- tasks
- policy decisions
- approval decisions
- routing decisions
- context scope
- audit trails
- final state changes

Hermes is only an execution backend behind `HermesAdapter`.

## Phase 1 Scope

Hermes may return:
- proposals
- reviews
- summaries
- research notes
- patch proposals

Hermes must not perform direct mutations in phase 1.

## Forbidden Operations

Hermes is explicitly forbidden from:
- `patch_apply`
- `command_execute`
- direct `file_write`
- direct task mutation
- direct `memory_write`
- direct cron creation

## Integration Flow

`Hub -> HermesAdapter -> Hermes API -> Parsed Ananta Artifact -> Hub review/approval`

Hermes output is untrusted until parsed and converted to Ananta artifacts.

## Rollout Phases

1. Phase 1:
`plan_only`, `review`, `summarize`, `patch_propose`.
2. Phase 2:
Optional `research_limited` with explicit URL/network policy.
3. Phase 3:
Optional delegated sandbox work only after separate approval and tests.

No phase allows direct Hermes `patch_apply` unless a future ADR replaces this rule.

## Wrong Implementations (Forbidden Shortcuts)

1. Let Hermes approve its own operations.
2. Route around Ananta policy checks when Hermes responds with "safe".
3. Accept raw Hermes text as execution proof without artifacts.
4. Allow Hermes to write files directly in the workspace.
5. Allow Hermes to create tasks or schedules directly in Hub state.
6. Send full repository dumps and secrets by default.
7. Enable Hermes automatically when API credentials are present.
