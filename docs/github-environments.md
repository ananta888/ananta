# GitHub Environments

Ananta uses GitHub Environments to keep validation, staging and release credentials scoped.

## Environments

### `staging`

Purpose:
- optional deployment or preview validation
- temporary credentials for pre-release checks
- non-production integration targets

Recommended protection:
- require at least one maintainer approval when deployment credentials are present
- restrict environment secrets to staging-only scope
- delete temporary secrets after the validation window

Recommended secrets:
- `DEPLOY_STAGING_TOKEN` only when staging deployment automation exists

### `release`

Purpose:
- GitHub Release publication
- future container image publication
- future signing or attestation

Recommended protection:
- require maintainer approval for manual release publication
- limit deployment branches/tags to `main` and `v*`
- prefer environment secrets over repository-wide secrets

Recommended secrets:
- `GHCR_TOKEN` only if package publishing cannot use `GITHUB_TOKEN`
- `COSIGN_KEY` only if keyless signing is not possible
- `DEPLOY_RELEASE_TOKEN` only for real production deployment automation

## Workflow Binding

The release workflow uses the `release` environment. If the environment is protected, GitHub will pause the job until the required approval is granted.

Nightly validation does not use release credentials and must not depend on release environment secrets.
