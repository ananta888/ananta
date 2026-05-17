# Goal-Scoped Config

## Purpose
Goal-scoped config isolates runtime configuration per goal and prevents global `/config` drift from changing in-flight execution.

## Precedence
Resolution order for the effective goal config snapshot:
1. `system_default`
2. `config_profile`
3. `goal_overrides`
4. `task_overrides`

## Snapshot Fields
At goal creation, `execution_preferences` stores:
- `config_snapshot`
- `config_snapshot_provenance`
- `config_snapshot_checksum`
- `config_snapshot_hash`
- `config_redaction_summary`

## Feature Flags
- `goal_scoped_config_enabled` (default `true`): enable snapshot resolution at goal submit.
- `goal_scoped_config_enforce_snapshot` (default `false`): when enabled and snapshots are disabled/missing, fail goal create.

## Rollout Stages
1. Observe: `enabled=true`, `enforce_snapshot=false`
2. Opt-in hardening: enable per environment, validate reports
3. Default target mode: runner uses `--config-mode goal_scoped`
4. Enforce: `enforce_snapshot=true` after compatibility evidence

## Rollback
1. Set `goal_scoped_config_enforce_snapshot=false`
2. If needed: set `goal_scoped_config_enabled=false`
3. Run acceptance runner with `--config-mode legacy_global_config` temporarily

## Troubleshooting
- `goal_config_source=global_fallback`: goal likely has no snapshot or invalid snapshot payload.
- Cross-goal provider/model drift: verify each run has distinct `config_checksum` and `config_profile` in acceptance report.
- Secrets exposure risk: snapshot API must only return redacted sensitive keys.
