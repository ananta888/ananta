# Autonomous Planning Learning Loop

This loop runs inside the hub and evaluates recent planning runs for stable failure or regression patterns.

## Policy

Use `planning_policy.learning_loop` to control:

- `enabled`
- `interval_seconds`
- `lookback_runs`
- `min_runs`
- `min_failures`
- `min_parse_success_rate`
- `min_validation_success_rate`
- `min_materialization_success_rate`
- `max_repair_rate`
- `candidate_activation_threshold`
- `rollback_threshold`
- `freeze_minutes`
- `canary_window_runs`
- `auto_activate`
- `require_review_before_activate`

## Behavior

- The loop summarizes recent runs per planning profile.
- If a profile shows stable degradation, it creates a candidate prompt version.
- If `auto_activate` is enabled, the candidate can be promoted to canary use.
- Canary candidates are monitored on subsequent runs.
- If canary quality regresses, the loop rolls back to the previous prompt version.

## Notes

- The loop is disabled by default.
- This keeps the feature additive and backwards compatible.
- The implementation stays in the hub control plane.
