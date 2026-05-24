# ADR: Native SSH Access via OIDC-Bound Short-Lived Certificates

## Status
Accepted

## Context
Ananta already provides OIDC login for browser terminal flows. Native SSH access is required for operator workflows, but plain SSH keys/passwords do not provide the required identity binding and governance guarantees.

## Decision
- Native SSH is an explicit optional access mode (`NATIVE_SSH_ENABLED=false` by default).
- OIDC identity must be validated first; issued SSH certificates are short-lived and policy-bound.
- OpenSSH certificate trust is enforced with `TrustedUserCAKeys` plus deterministic principals.
- Managed terminal users must use `ForceCommand` to enter `AnantaSshTerminalWrapper`.
- Existing OIDC implementation (`agent/routes/auth_oidc.py`) remains the source for OpenID Connect behavior.
- First operational provider path uses Keycloak OIDC + step-ca style backend abstraction; no CA private key exposure to workers/LLMs.

## Alternatives considered
- Teleport as first backend: strong platform, but heavier operational footprint for initial rollout.
- Internal SSH CA immediately: rejected for first rollout due to higher key-management and audit burden.

## Consequences
- Clear separation between browser terminal and native SSH modes.
- Better revocation posture via short TTL certificates.
- Additional infrastructure components (OIDC provider and CA backend) required when native SSH is enabled.
