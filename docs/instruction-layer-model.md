# Instruction Layer Model (UPT)

This document defines the instruction stack for user profile prompts and task overlays.

## Terminology

- `user_profile`: persistent user operating preferences
- `task_overlay`: scoped instructions for goal/task/session/usage
- `instruction_stack`: effective runtime assembly of all active layers

## Layers and precedence

High to low precedence:

1. `governance` (hub policy, security and approval constraints, never overridable)
2. `blueprint_template` (team/role template system prompt)
3. `user_profile` (persistent user operating preferences)
4. `task_overlay` (task/goal/session scoped overlay)
5. `task_input` (current user request)

Conflicts are resolved by precedence. Overlay preferences override profile preferences.

## Blueprint interaction rules

- `blueprint_template` stays above all user-controlled layers.
- `user_profile` and `task_overlay` may refine style and execution expression, but not governance or template hard constraints.
- If profile and overlay disagree, `task_overlay` wins for shared preference keys.

## Allowed user influence scope

User-controlled layers may influence:

- style
- language
- detail level
- working mode
- formatting

## Forbidden user influence scope

User-controlled layers may not override:

- approval policy or approval requirements
- governance mode or security policy
- execution risk policy
- allowed tools
- write access and runtime execution constraints

Conflicts are blocked backend-side with explicit `instruction_policy_conflict` responses.

## Redaction and storage strategy

- Profile and overlay text is stored as authored content for deterministic replay.
- Audit trails persist identifiers and selection metadata, not full prompt bodies.
- Generic redaction (`agent.common.redaction`) applies to structured audit payloads and API responses where relevant.

## Read-model visibility

- Task and goal read models expose `instruction_layers` with:
  - `owner_username`, `profile_id`, `overlay_id`
  - `selected_profile` and `selected_overlay` summaries
  - resolved `attachment_kind` / `attachment_id` when available
- Overlay responses include lifecycle visibility (`kind`, `consumed_count`, `remaining_uses`, expiry flags).

## Policy conflict feedback

When user-controlled layers violate forbidden scope, the API responds with `instruction_policy_conflict` and structured payload fields:

- `reason`
- `policy_domain`
- `forbidden_directives`
- `forbidden_metadata_keys`
- `allowed_scope`
- `forbidden_scope`
- `suggested_fix`

## Role/template compatibility checks

- Effective stack diagnostics now include `template_compatibility`.
- Compatibility can return `ok`, `warn` or `block`.
- `warn` signals soft mismatches (for example implementation-focused profile on review template context).
- `block` is triggered when profile/overlay compatibility metadata explicitly disallows the detected role/template context.
- Task selection API (`/tasks/{task_id}/instruction-selection`) rejects blocked compatibility with `instruction_template_incompatible`.

## Overlay attachment points

Supported attachment kinds:

- `task`
- `goal`
- `session`
- `usage`

First practical release focuses on task/goal/session.

## Overlay lifecycle modes

Supported `scope` values:

- `task`
- `goal`
- `session`
- `usage`
- `one_shot`
- `project`

Lifecycle behavior:

- `one_shot`: overlay is consumed on first runtime application and auto-deactivated.
- `session`: overlay should be bound to `attachment_kind=session` and a session id.
- `project`: overlay should be bound to `attachment_kind=usage` with a project usage key.

Lifecycle state is visible in API read models via the `lifecycle` object (`kind`, `started_at`, `expires_at`, `is_expired`, `consumed_count`, `remaining_uses`).

## Safe defaults

- No profile selected: use governance + template + task input.
- No overlay selected: use governance + template + optional profile + task input.

## Related guides

- [Instruction Layer Authoring Guide](./instruction-layer-authoring-guide.md)
- [Instruction Layer Golden Path](./instruction-layer-golden-path.md)
- [Instruction Layer Rollout Plan](./instruction-layer-rollout-plan.md)
