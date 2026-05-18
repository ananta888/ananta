# API: Goal Effective Config

## Endpoint

```
GET /goals/{goal_id}/effective-config
Authorization: Bearer <token>
```

Returns the effective configuration snapshot for a goal. Requires authentication. Any authenticated user may read the config for a goal without a `team_id` restriction.

## Response Shape

```json
{
  "status": "ok",
  "data": {
    "goal_id": "abc123",
    "goal_config_source": "snapshot",
    "config_checksum": "sha256-hex-64-chars",
    "config_snapshot": {
      "version": "goal_config_snapshot.v1",
      "profile_id": "ananta_ollama_local",
      "config": {
        "default_provider": "ollama",
        "default_model": "ananta-default:latest",
        "llm_config": {
          "base_url": "http://ollama:11434/api/generate",
          "api_key": "***REDACTED***"
        }
      },
      "provenance": {
        "resolution_order": ["system_default", "profile", "goal", "task"],
        "field_sources": {
          "default_provider": "profile",
          "default_model": "goal"
        }
      }
    },
    "redaction_summary": {
      "redacted_fields": 1,
      "redacted_paths": ["llm_config.api_key"]
    }
  }
}
```

## Key Fields

| Field | Type | Description |
|-------|------|-------------|
| `goal_config_source` | `"snapshot"` \| `"global_fallback"` | `"snapshot"` means the goal has an immutable per-goal config; `"global_fallback"` means it was created before goal-scoped config was enabled. |
| `config_checksum` | `string (sha256)` | SHA-256 of the redacted config snapshot. Identical for two goals created with the same profile and non-secret overrides. |
| `config_snapshot.config` | `object` | The merged effective config. Secret fields are replaced with `"***REDACTED***"`. |
| `config_snapshot.provenance.field_sources` | `object` | Maps each overridden key to its source layer: `"profile"`, `"goal"`, or `"task"`. Keys not overridden (system defaults) do not appear here. |
| `redaction_summary.redacted_fields` | `integer` | Number of fields that were redacted. Used to detect accidental secret exposure. |

## Notes for Frontend Renderers

- **Profile ID**: read from `config_snapshot.profile_id`. May be `null` for legacy goals.
- **Checksum**: show `config_checksum` as a short fingerprint (first 8 chars is enough for display). Use full value for equality checks.
- **Field provenance**: `config_snapshot.provenance.field_sources` tells you which layer each setting came from — use this to explain to users why a particular model was selected.
- **Redaction summary**: never render `***REDACTED***` values as real values. Show a lock icon or "hidden" label instead.
- **`config_checksum` vs `config_snapshot_hash`**: these are the same field. The API exposes it as `config_checksum`. The stored field in `execution_preferences` is `config_snapshot_checksum`.

## Error Cases

| HTTP | `message` | Meaning |
|------|-----------|---------|
| 401/403 | `unauthorized` | Missing or invalid token. |
| 404 | `goal_not_found` | Goal ID does not exist. |
