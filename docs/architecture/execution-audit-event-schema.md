# Execution Audit Event Schema (GEC-T021/T022/T024)

Canonical schema: `execution_audit_event.v1`

Required fields:

- `trace_id`
- `goal_id`
- `task_id`
- `actor_role`
- `operation_type`
- `outcome`
- `timestamp`

Common operation types:

- `tool_intent_remap`
- `security_policy_block`
- `approval_block`
- `execution_result_finalize`

## Prompt audit redaction and retention policy

Default policy (metadata-only):

- prompt content storage: disabled
- redaction: required
- output details: redacted metadata only

Debug policy:

- prompt content storage: disabled by default
- explicit opt-in required in restricted environments

Restricted forensic policy:

- prompt content storage: allowed only under restricted controls and explicit governance approval

