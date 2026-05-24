# Terminal Audit (SSH + Web Terminal)

Ananta records terminal security-relevant lifecycle events for both web terminal and native SSH access.

## Objectives

- Correlate identity, policy decision, and session lifecycle.
- Preserve forensic value without logging secrets.
- Keep hub-owned audit flow; workers do not own cross-session orchestration.

## SSH audit events

Implemented in `agent/services/ssh_access_audit_service.py`.

- `ssh_certificate_issued`
- `ssh_certificate_issuance_denied`
- `ssh_terminal_session_created`
- `ssh_terminal_session_attached`
- `ssh_terminal_session_detached`
- `ssh_terminal_session_write`
- `ssh_terminal_session_killed`
- `ssh_terminal_policy_denied`

## Required correlation fields

- `user_id`
- `auth_source`
- `target_type`
- `target_id` (when available)
- `session_id` (for runtime events)
- `decision_id` (policy/certificate flow)
- `policy_version`
- `key_id` / `principal` (certificate path)

## Data minimization rules

Never store:

- OIDC access tokens or refresh tokens
- raw bearer tokens
- private SSH key material
- passwords

## Operational notes

- Enable via `SSH_AUDIT_ENABLED=true`.
- Keep native SSH disabled by default (`NATIVE_SSH_ENABLED=false`).
- Use short-lived SSH cert TTLs and strict principal mapping.
