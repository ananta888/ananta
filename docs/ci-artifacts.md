# CI Artifacts

This document defines the artifact contract for GitHub Actions. The goal is fast diagnosis without guessing which job produced which file.

## Naming

Artifact names use the `ananta-` prefix and describe the diagnostic surface:

- `ananta-compose-configs`: rendered Docker Compose configurations from the compose validation job.
- `ananta-release-verification-report`: release-gate evidence for the current commit.
- `ananta-e2e-compose-results`: Playwright and E2E failure output from the compose-backed E2E job.
- `ananta-architecture-diagrams`: rendered architecture diagrams.
- `ananta-github-release-assets`: prepared GitHub Release asset bundle from the release workflow.

New artifacts should follow the same pattern: `ananta-<area>-<content>`.

## Paths

Workflow-generated diagnostic files should be written under a stable workspace path before upload:

- `ci-artifacts/compose/` for rendered compose configurations.
- `frontend-angular/test-results/` for Playwright/E2E reports, screenshots, traces, and summaries.
- `architektur/rendered/` for generated architecture diagrams.

Avoid `/tmp` for files that are useful after a failed run. `/tmp` is acceptable only for short-lived internal scratch data.

## Failure Diagnosis

For a failed run, inspect artifacts in this order:

1. `ananta-release-verification-report` for release-gate or reproducibility failures.
2. `ananta-e2e-compose-results` for browser failures, traces, screenshots, and `failure-summary.md`.
3. `ananta-compose-configs` for rendered service configuration differences.
4. `ananta-architecture-diagrams` for documentation rendering failures.
5. `ananta-github-release-assets` for prepared release payload inspection.

If a workflow creates a new class of diagnostic output, update this document in the same pull request.
