# Instruction Layer Rollout Plan

This rollout introduces profile and overlay layers additively without breaking existing task flows.

## Phase 1: Backend availability (default-safe)

- Enable profile/overlay persistence and APIs.
- Keep behavior unchanged when no profile/overlay is selected.
- Enforce backend governance conflict blocking.

## Phase 2: Controlled adoption

- Expose profile presets (`/instruction-profiles/examples`) for safe onboarding.
- Encourage task-level overlays first (lowest risk and highest observability).
- Use diagnostics endpoints for operator verification.

## Phase 3: Wider integration

- Expand usage in goal/task creation flows with explicit selection fields.
- Keep `instruction_context` explicit in read models to avoid hidden behavior.
- Maintain audit events for create/update/select/attach/detach operations.

## Phase 4: Operational hardening

- Track conflict rates and suppressed-layer events.
- Review redaction and data retention policy for instruction content.
- Add migration script for existing PostgreSQL deployments (new instruction tables).

## Backward compatibility principles

- Existing clients continue to work without profile/overlay fields.
- New fields remain optional.
- Governance remains highest precedence in all phases.
