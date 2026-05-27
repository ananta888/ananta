# KRITIS Audit Smoke Checklist

Use this checklist before release sign-off or evidence export.

## Preconditions

1. Admin token available.
2. Representative test goal/task exists.
3. Time window for the smoke run is known.

## Checklist

1. **Read/query surface**
   - Call `GET /api/system/audit-logs` with filters: `task_id`, `trace_id`, `actor`, `event_class`, `since`, `until`.
   - Confirm expected events are returned and sensitive payloads stay redacted.
2. **High-risk write flow**
   - Execute one controlled write-capable operation.
   - Confirm `write_operation` event exists with task/trace linkage.
3. **Approval and blocked case**
   - Trigger one approval-required path and one blocked/denied path.
   - Confirm approval and blocked outcomes are both visible in audit events.
4. **Workflow transition**
   - Complete at least one state transition.
   - Confirm transition event contains `from_state`, `to_state`, and trigger context.
5. **Summary read-model**
   - Call `GET /api/system/audit-logs/summary`.
   - Confirm summary is compact and critical event count is plausible for the run.
6. **Integrity check**
   - Call `GET /api/system/audit-logs/integrity`.
   - Confirm `tamper_evident_ok=true` and no invalid hash IDs.

## Evidence package

Store together:

- Filtered audit export for the test window
- Summary endpoint response
- Integrity endpoint response
- Scenario notes (what was approved, blocked, and written)

