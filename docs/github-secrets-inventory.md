# GitHub Secrets Inventory

This inventory defines the expected GitHub Actions and release-process secrets. It does not contain secret values.

Prefer GitHub Environment secrets over repository-wide secrets for release or deployment scopes. Repository-level secrets should be limited to non-deployment automation that must run on ordinary CI jobs.

## Current Required Secrets

| Name | Scope | Purpose | Owner | Rotation |
| --- | --- | --- | --- | --- |
| `GITHUB_TOKEN` | GitHub-provided | Checkout, artifact upload and GitHub Release creation through workflow permissions. | GitHub Actions | Managed by GitHub |

The current CI and release workflows use deterministic test-only environment values for Compose validation and release-gate execution. Those values are not production credentials and should not be reused outside CI validation.

## Future Environment Secrets

| Name | Recommended Scope | Purpose | Owner | Rotation |
| --- | --- | --- | --- | --- |
| `GHCR_TOKEN` | `release` environment | Publish container images to GHCR if release images become official assets. | Release maintainer | Rotate after maintainer changes or suspected exposure |
| `COSIGN_KEY` / keyless identity | `release` environment | Sign images or artifacts if signing is adopted. | Security maintainer | Prefer keyless; rotate keys after each incident |
| `DEPLOY_STAGING_TOKEN` | `staging` environment | Optional staging deployment automation. | Operations maintainer | Rotate at least quarterly |
| `DEPLOY_RELEASE_TOKEN` | `release` environment | Optional production or release deployment automation. | Operations maintainer | Rotate before major releases or maintainer changes |

## Hygiene Rules

- Do not put production credentials in workflow `env` blocks.
- Do not promote test-only placeholder values into deployment secrets.
- Use environment protection rules for `release` before adding publish or deployment credentials.
- Keep token scopes narrow: read-only for validation, package write only for image publishing, release write only for GitHub Release creation.
- Remove unused secrets during release retrospectives.
- Record new required secrets here in the same PR that introduces the workflow dependency.

## Review Checklist

Before adding or changing a secret-dependent workflow:

1. Confirm the workflow cannot use `GITHUB_TOKEN` with narrower `permissions`.
2. Confirm the secret belongs to a protected GitHub Environment when it affects release, deployment or publishing.
3. Document the owner and rotation expectation above.
4. Add a PR note explaining why the credential is necessary.
