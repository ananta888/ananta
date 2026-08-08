# Supply Chain Checks

Ananta uses pinned release inputs and explicit dependency review as complementary controls.

## Direct-to-main Dependency Updates

Automated Dependabot pull requests are disabled to preserve the repository's
single-branch policy. Dependency updates are prepared in a clean local
`main` worktree and pushed directly to `main` only after review and focused
verification.

GitHub Actions, Python lockfiles and frontend npm lockfiles remain explicit
review targets. Dependency updates that affect auth, CI, release, Docker or
worker execution are treated as security-sensitive.

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
