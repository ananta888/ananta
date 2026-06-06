# Model Routing Profiles Migration

## Existing Deployments

No immediate migration is required. Existing deployments that only set
`DEFAULT_PROVIDER` and `DEFAULT_MODEL` continue to work.

```bash
DEFAULT_PROVIDER=lmstudio
DEFAULT_MODEL=auto
```

These values are used when no usable model profile is configured.

## Add Profile Mode

1. Choose an example profile file from `config/models/examples/`.
2. Copy it to your deployment config location.
3. Set:

```bash
MODEL_PROFILES_PATH=/etc/ananta/model_profiles.yaml
```

4. Keep `DEFAULT_PROVIDER` and `DEFAULT_MODEL` during rollout as documented
   fallback values.

## Cloud Profile Requirements

Cloud profiles must define:

- `cloud: true`
- `cloud_allowed: true`
- `block_secret_context: true`
- `api_key_env`, never a literal API key value

Secret or customer-confidential context blocks cloud candidates before the call.

## Legacy Overrides

Existing role/template/task-kind overrides stay compatible. The normalization
service accepts legacy strings and maps provider aliases such as `lm_studio`,
`lm-studio` and `local` to `lmstudio`.

## Rollout Checklist

- Start with local-only profiles.
- Verify `GET /config/model-routing/read-model`.
- Run a public-context test task and inspect `model_profile_id`.
- Run a secret-context test task and confirm cloud candidates are blocked.
- Only then add optional cloud reviewer/summarizer profiles.

## Rollback

Unset `MODEL_PROFILES_PATH` and keep legacy defaults:

```bash
unset MODEL_PROFILES_PATH
DEFAULT_PROVIDER=lmstudio
DEFAULT_MODEL=auto
```

No task data migration is required for rollback because profile routing is
resolved at runtime.
