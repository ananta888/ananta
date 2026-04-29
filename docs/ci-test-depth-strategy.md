# CI Test Depth Strategy

Ananta separates validation depth by decision point. The goal is to keep pull requests reviewable while still running heavy release signals before tags.

## Pull Request Gates

Required pull request gates should be fast enough for normal review:

- backend static and standard checks
- backend smoke regression set
- frontend production build
- Docker Compose config rendering
- release-gate report without image builds
- todo and docs link validation

These checks are the recommended branch-protection required checks in `docs/github-admin-setup.md`.

## Optional Pull Request Signals

The following jobs provide useful context but should not block every small change until their runtime is stable:

- compose-backed E2E
- architecture diagram rendering
- expensive browser diagnostics

`architecture-diagrams` runs in `.github/workflows/quality-and-docs.yml` only on push/manual runs and on pull requests labeled `full-ci`.
`e2e-compose` runs in `.github/workflows/e2e-compose.yml` on weekly schedule, manual trigger, pushes to `main`, and pull requests labeled `full-ci`.
This keeps ordinary pull requests focused on required merge gates while preserving an explicit opt-in path for deeper validation.

They can become required later when their failure rate and runtime are predictable.

## Nightly Validation

`.github/workflows/nightly-rc-validation.yml` runs the same release-adjacent checks on a schedule and on demand. Use it to catch slower regressions without making every pull request expensive.

## Release Candidate Validation

Release candidates should run:

- strict release gate with Compose config validation
- image build validation where release images are relevant
- SBOM generation
- release asset preparation
- open flaky issue review from `docs/ci-flaky-tracking.md`

Do not publish a stable tag until release-candidate evidence is attached to the release decision.
