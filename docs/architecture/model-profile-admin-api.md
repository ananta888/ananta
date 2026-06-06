# Model Profile Admin API

Status: prepared design, write access disabled by default.

## Read path

Operators inspect profile routing through:

- `GET /config/model-routing/read-model`
- `GET /dashboard/read-model`

Both responses redact secrets. Profiles expose `api_key_env` only; plaintext API
keys are never returned.

## Future write path

Writable profile administration should be introduced behind an explicit feature
flag such as `MODEL_PROFILE_ADMIN_WRITES_ENABLED=true`.

Proposed endpoints:

- `POST /config/model-profiles/validate`
- `PUT /config/model-profiles`
- `PATCH /config/model-profiles/{profile_id}`

Validation must run before persistence using the existing model profile loader
and schema checks. Cloud profiles must explicitly set:

- `cloud_allowed`
- `block_secret_context`
- `api_key_env`

Plaintext fields named `api_key`, `secret`, `password`, or `token` are rejected.

## Audit

Every accepted write must emit an audit event with:

- actor
- profile ids changed
- before/after config hash
- validation result
- review id when review is required

The event must not include plaintext secrets.

## Governance

Default behavior remains read-only. Production deployments should require admin
auth and review approval for cloud profile changes. This preserves the hub as
the policy owner; workers only consume delegated runtime targets.
