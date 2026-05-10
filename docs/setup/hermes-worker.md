# Hermes Worker Setup

## What Hermes Can Do

Hermes is an external proposal/review worker in Ananta. It can produce:
- plan proposals
- reviews
- summaries
- patch proposals
- limited research summaries from provided context

Hermes cannot:
- execute shell commands
- apply patches
- write files directly
- mutate tasks or schedules
- mutate memory

`patch_apply` and `command_execute` remain native Ananta approval-gated paths.

## Modes

### Disabled (default)

```json
{
  "feature_flags": {
    "enable_hermes_worker_adapter": false
  },
  "hermes_worker_adapter": {
    "enabled": false
  }
}
```

### Local Endpoint

```json
{
  "feature_flags": {
    "enable_hermes_worker_adapter": true
  },
  "hermes_worker_adapter": {
    "enabled": true,
    "base_url": "http://localhost:8800",
    "api_key_env": "HERMES_API_KEY",
    "default_model": "qwen2.5-coder-7b",
    "timeout_seconds": 20,
    "max_retries": 1,
    "allowed_task_kinds": ["plan_only", "review", "summarize", "patch_propose", "research_limited"],
    "blocked_task_kinds": ["patch_apply", "command_execute", "service_mutation", "config_mutation"],
    "cloud_allowed": false,
    "strict_json_required": true
  }
}
```

### Cloud Endpoint (opt-in warning)

Use this only with explicit policy decision:
- `model_policy.cloud_allowed=true`
- sensitive context restrictions understood

```json
{
  "feature_flags": {
    "enable_hermes_worker_adapter": true
  },
  "hermes_worker_adapter": {
    "enabled": true,
    "base_url": "https://api.hermes.example",
    "api_key_env": "HERMES_API_KEY",
    "default_model": "gpt-oss-20b-coder",
    "cloud_allowed": true,
    "allowed_task_kinds": ["plan_only", "review", "summarize", "patch_propose"],
    "blocked_task_kinds": ["patch_apply", "command_execute"]
  }
}
```

## Optional Compose Overlay

Use `docker-compose.hermes-worker.yml` only when you want a local Hermes container:

```bash
docker compose -f docker-compose.base.yml -f docker-compose.hermes-worker.yml up -d
```

This overlay is optional and not required for normal Ananta startup.

## Troubleshooting

- `disabled`: feature flag or adapter disabled.
- `unauthorized`: API key missing/invalid.
- `unavailable`: endpoint unreachable or timeout.
- `cloud_blocked`: endpoint classified as cloud while `cloud_allowed=false`.
- `parse_error`: Hermes response not valid strict JSON schema.
- `policy_denied`: task mode/capability blocked by policy.

Diagnostics fields:
- `enabled`
- `feature_flag_enabled`
- `health_state`
- `endpoint_classification`
- `selected_default_model`
- `allowed_task_kinds`
- `blocked_task_kinds`
- `last_error_code`
- `cloud_allowed`
