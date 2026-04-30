# Workflow Credential Boundary

Date: 2026-04-30

- Credentials stay in provider secret stores or explicit secret refs.
- Task payloads/logs/artifacts must never contain secret values.
- `secret_ref` is allowed; plaintext secret values are forbidden.
- Callback verification must use scoped tokens or HMAC signatures.
- Least privilege is required for integration webhooks/tokens.
