# Secrets Rotation And Minimal Permissions

This document turns the secrets inventory into an operating procedure. It does not contain secret values.

## Permission Model

- Default CI validation should use `GITHUB_TOKEN` with `contents: read`.
- GitHub Release publication may use `GITHUB_TOKEN` with `contents: write`.
- Package publishing, deployment and signing credentials must be scoped to a protected GitHub Environment before use.
- Production or release credentials must not be stored as repository-wide secrets unless no environment-scoped alternative exists.

## Rotation Triggers

Rotate affected credentials when:

- a maintainer with access leaves the project
- a workflow or runner is suspected to be compromised
- a secret appears in logs, artifacts, issues or PR comments
- release or deployment ownership changes
- a credential scope is broadened or narrowed
- a major release readiness review identifies stale automation access

## Rotation Cadence

| Secret Class | Cadence |
| --- | --- |
| GitHub-provided `GITHUB_TOKEN` | Managed by GitHub |
| Package publish tokens | At least quarterly or after ownership changes |
| Deployment tokens | At least quarterly and before stable major releases |
| Signing keys | Prefer keyless; otherwise rotate after incidents and ownership changes |
| Temporary staging credentials | Delete after the validation window |

## Incident Response

1. Revoke or rotate the affected credential.
2. Disable any workflow depending on the credential if impact is unclear.
3. Inspect recent workflow runs and release artifacts.
4. Re-run release verification after remediation.
5. Update `docs/github-secrets-inventory.md` if ownership, scope or rotation changed.
