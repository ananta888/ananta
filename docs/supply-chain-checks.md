# Supply Chain Checks

Ananta uses pinned release inputs and GitHub-side dependency visibility as complementary controls.

## GitHub Dependency Visibility

`.github/dependabot.yml` enables scheduled update discovery for:

- GitHub Actions
- Python dependencies rooted at `requirements.lock` and `requirements-dev.lock`
- frontend npm dependencies rooted at `frontend-angular/package-lock.json`

Dependabot PRs must still pass the regular quality workflow and should be reviewed like any other supply-chain change. Dependency updates that affect auth, CI, release, Docker or worker execution should be treated as security-sensitive.

## Locked Inputs

Release gates continue to enforce:

- exact Python runtime version
- exact Node runtime version
- Python lockfiles
- npm lockfile
- pinned GitHub Actions
- digest-pinned public container image references
- fixed apt snapshots

## Review Rules

Before merging a dependency update:

1. Confirm the package is still required.
2. Review changelog or release notes for security, licensing and runtime behavior changes.
3. Confirm lockfile changes match the manifest change.
4. Confirm no broad workflow permissions were added.
5. Confirm release-gate assumptions still hold for release-critical dependencies.
