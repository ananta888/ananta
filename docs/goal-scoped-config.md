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

## Example: Create a Goal with opencode_ollama_local Profile

```json
POST /goals
{
  "goal": "Implement a Fibonacci API with tests",
  "execution_preferences": {
    "config_profile": "opencode_ollama_local",
    "config_overrides": {
      "default_model": "ananta-default:latest"
    }
  }
}
```

The server resolves and stores the effective config snapshot at creation time. After the goal is created, the snapshot is **immutable** — changing the global `/config` does not affect this goal's execution.

## Snapshot Immutability

Once a goal is created, `execution_preferences.config_snapshot` is frozen. It is never updated during planning or execution. This ensures:
- The provider/model that was selected at creation time is always used, regardless of global config changes.
- The `config_snapshot_checksum` remains stable and can be used to verify nothing drifted.
- Two goals created at different times with the same profile and overrides will have the same checksum (assuming the profile itself has not changed).

## Allowed Config Keys

Only keys in `ALLOWED_GOAL_CONFIG_KEYS` (see `agent/services/goal_config_resolver_service.py`) are accepted in `config_overrides`. The current allowed keys include:
`default_provider`, `default_model`, `llm_config`, `llm_profile_policy`, `sgpt_routing`, `opencode_runtime`, `task_timeout_seconds`, `max_retries`, `verification_policy`.

**Unknown keys are rejected at goal creation** with HTTP 400 (`invalid_goal_config_key`). The response body lists the offending keys in `data.unknown_keys`. This prevents silent misconfiguration.

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
