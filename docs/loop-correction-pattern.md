# Loop Correction Pattern (optional mitigation)

## Scope

This note defines how Ananta can inject corrective guidance when a doom-loop signal is detected, without introducing hidden automation.

## Trigger

- Input comes from the generic detector (`loop_detection` payload on execution history).
- Correction is only considered when detector action is `inject_correction` (or stronger levels that downgrade to correction in conservative mode).
- Hub policy remains the owner of mitigation decisions.

## Correction payload shape

The correction is attached as explicit structured data (not silent prompt rewriting):

- `loop_correction.id`
- `loop_correction.classification`
- `loop_correction.reasons[]`
- `loop_correction.required_adjustments[]`
- `loop_correction.expiry_signals`
- `loop_correction.created_at`

## Bounded behavior

To prevent self-reinforcing mitigation loops:

- Max injections per task: `max_corrections_per_task` (default conservative: 2)
- Cooldown window: `correction_cooldown_signals` (default: 3 signals)
- Dedup key: same correction class + reason fingerprint is not injected twice in cooldown
- Auto-stop: after budget exhaustion, escalation changes to `require_review`

## Visibility and auditability

- Every correction emits a `loop_correction_injected` task history event.
- The event includes detection reference, correction payload summary and policy source.
- Audit trail stores a matching control-plane event (`loop_correction_injected`) with `task_id` and `trace_id`.

## No hidden mitigation rule

Correction must always be operator-visible in history/read-model outputs.
No silent mutation of execution context is allowed.
