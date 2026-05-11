# Hermes Worker Setup

## What Hermes Can Do

Hermes is an external proposal/review worker in Ananta. It operates as a governed worker adapter — it can produce read-only artifacts but never mutates system state.

Hermes can:
- `plan_only` — structured plan proposals
- `review` / `code_review` — code or artifact reviews (equivalent, both allowed)
- `summarize` — summaries from provided context
- `patch_propose` — diff proposals (requires human approval before apply)
- `research_limited` — research claims from provided context (no open network)

Hermes cannot:
- execute shell commands (`shell_execute`, `shell_execution` both denied)
- apply patches (`patch_apply`)
- write files or mutate workspace
- mutate tasks, memory, cron schedules, or services
- make unrestricted network calls

`patch_apply` and `command_execute` remain native Ananta approval-gated paths.

## Rollout Gates (Phase 1)

Both switches must be `true` before Hermes executes anything:

| Config key | Default | Effect |
|---|---|---|
| `feature_flags.enable_hermes_worker_adapter` | `false` | Routing-level gate (tool router) |
| `hermes_worker_adapter.feature_flag_enabled` | `false` | Adapter-level gate (must be explicit opt-in) |

Neither defaults to `true`. If either is `false`, the adapter returns `degraded` and does not make any network call.

## Configuration

### Disabled (default — no changes required)

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
    "feature_flag_enabled": true,
    "base_url": "http://localhost:8800",
    "api_key_env": "HERMES_API_KEY",
    "default_model": "qwen2.5-coder-7b",
    "timeout_seconds": 20,
    "max_retries": 1,
    "allowed_task_kinds": ["plan_only", "review", "summarize", "patch_propose", "research_limited"],
    "blocked_task_kinds": ["patch_apply", "command_execute", "service_mutation", "config_mutation"],
    "cloud_allowed": false,
    "strict_json_required": true,
    "default_temperature": 0.1
  }
}
```

Local endpoints can process all sensitivity levels (including `secret`, `confidential`). The sensitivity filter only applies to cloud endpoints.

### Cloud Endpoint (opt-in — read policy implications first)

Use this only with an explicit policy decision:
- `model_policy.cloud_allowed=true` must be set in the envelope
- `hermes_worker_adapter.cloud_allowed=true` must be set in config
- Both must be `true` (effective_cloud_allowed = config AND envelope)
- Secret and confidential context blocks are never sent to cloud endpoints

```json
{
  "feature_flags": {
    "enable_hermes_worker_adapter": true
  },
  "hermes_worker_adapter": {
    "enabled": true,
    "feature_flag_enabled": true,
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

## Running the Test Suite

```bash
# Core adapter behavior (32 tests)
python -m pytest tests/test_hermes_worker_adapter_track.py -q

# Security regressions: no-network/no-file side effects + cloud×sensitive matrix (18 tests)
python -m pytest tests/test_hermes_adapter_security.py -q

# Output schema validation per mode (17 tests)
python -m pytest tests/test_hermes_parser.py -q

# All Hermes-related tests
python -m pytest tests/test_hermes_worker_adapter_track.py tests/test_hermes_adapter_security.py tests/test_hermes_parser.py -q
```

Expected: 67 passed, 0 failed, no network calls made during tests.

## Troubleshooting

| Reason code | Meaning |
|---|---|
| `disabled_config` | `hermes_worker_adapter.enabled = false` |
| `disabled_by_feature_flag` | `feature_flag_enabled = false` on adapter |
| `unauthorized` | API key missing or invalid (HTTP 401) |
| `unavailable` | Endpoint unreachable or timeout |
| `cloud_blocked` | Endpoint classified as cloud but `cloud_allowed=false` |
| `sensitivity_blocked` | Context contains secret/confidential blocks for cloud endpoint |
| `task_kind_not_allowed` | Mode not in `allowed_task_kinds` |
| `task_kind_blocked` | Mode in `blocked_task_kinds` |
| `missing_capability` | Envelope capability grant does not include required capability |
| `parse_error_*` | Hermes response did not pass strict JSON schema validation |
| `hermes_invalid_json_response` | HTTP response body is not valid JSON |
| `context_missing_or_sensitive` | No includable context blocks after filtering |
| `research_context_missing` | `research_limited` mode requires at least one context block |

## Diagnostics

Call the adapter's `diagnostics()` method or the `/diagnostics` endpoint to inspect:

- `enabled` — admin on/off switch
- `feature_flag_enabled` — rollout gate
- `health_state` — last health probe result
- `endpoint_classification` — `local`, `private_network`, or `cloud`
- `selected_default_model` — configured default model
- `allowed_task_kinds` — modes the adapter will accept
- `blocked_task_kinds` — modes that are hard-denied
- `last_error_code` — most recent error code from client or parser
- `cloud_allowed` — whether cloud dispatch is configured
