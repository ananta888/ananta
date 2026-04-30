# Security and Governance Decisions: Auth Provider Profile

This note documents the current auth-provider profile contract and its governance intent.

## Scope

- Runtime config key: `auth_provider`
- Allowed values: `local`, `oidc_bff`
- Default: `local`
- Update endpoint: `POST /config` (admin-only)

## Decision

- `local` keeps the existing built-in username/password + JWT flow.
- `oidc_bff` is a bounded profile toggle for browser-safe session architectures (BFF pattern).
- The profile toggle is additive and backward-compatible: existing clients remain valid with `local`.

## Security Rationale

- The profile is explicitly validated at config boundaries.
- Invalid provider values are rejected with `invalid_auth_provider`.
- The config read-model inventory exposes allowed values to avoid client-side guesswork.

## Governance Rationale

- Hub-owned policy remains unchanged: only admin users can mutate config.
- This keeps auth-mode rollout auditable and controlled while preserving API compatibility.

## SOLID check

- SRP: Provider selection state is separated from auth token verification internals.
- OCP: New auth profiles can be added as explicit enum values instead of mutating existing behavior implicitly.
- DIP: Consumers use the config contract (`auth_provider`) instead of concrete route internals.
