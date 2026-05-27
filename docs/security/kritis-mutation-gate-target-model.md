# KRITIS MutationGate Target Model

## Scope

MutationGate is the single backend boundary for mutation-capable execution. It runs before write-like execution and reuses existing approval and execution-risk policy decisions.

## Mutation classes (taxonomy)

MutationGate maps actions into explicit classes:

- `read_only`
- `file_write`
- `patch_apply`
- `artifact_mutation`
- `task_state_mutation`
- `shell_write_effect`
- `install_remove`
- `repair_action`
- `system_mutation`
- `admin_mutation`

Defaults and policy expectations:

- `read_only`: allow by default.
- `file_write`, `patch_apply`, `artifact_mutation`, `task_state_mutation`, `shell_write_effect`, `repair_action`: at least scoped confirmation path.
- `install_remove`, `system_mutation`, `admin_mutation`: high-risk classes; expected to be blocked or strongly constrained by governance mode and risk policy.

## Normalized mutation target model

Before decisioning, MutationGate normalizes mutation targets into a stable structure:

- `target_type` (`path`, `artifact`, `none`, ...)
- canonicalized `path` where available
- `artifact_id`, `task_id`, `service_name`, `system_resource`
- `project_scope` (goal scope)
- `target_fingerprint` (hash over normalized target fields)

This limits path-variant bypasses and gives deterministic approval binding/audit correlation.

## Scoped mutation approval model

Scoped approval can be attached via `task.mutation_approval` and can bind:

- `task_id`
- `trace_id`
- `mutation_classes`
- `target_fingerprint`
- `expires_at`
- optional `actor`

Validation is fail-closed for present-but-invalid scope:

- expired scope
- mismatched task/trace/actor
- mismatched mutation class
- mismatched target fingerprint
- incomplete scope payload

Legacy `approval_confirmed` remains a compatibility fallback where no scoped payload exists.

## Service decision contract

`MutationGateService.evaluate(...)` returns:

- `allow`
- `confirm_required`
- `blocked`

with structured reason codes, normalized target payload, and scope context.

