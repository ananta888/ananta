# Release Dependency Locking

This document defines the dependency locking policy for the v1.0.0 release track.

## Python

`pyproject.toml` remains the developer-facing project metadata source. It may stay readable and grouped by purpose.

Release builds must use a dedicated lock artifact:

- `requirements.lock` for backend runtime dependencies
- `requirements-dev.lock` for CI-only test and lint dependencies

`requirements.txt` is runtime source input and `requirements-dev.txt` is dev/CI source input. Docker runtime images must install from `requirements.lock` and must not install dev-only tools unless a specific image target documents why it needs them.

The intended release flow is:

1. Keep runtime dependency names in `pyproject.toml` and `requirements.txt`.
2. Keep test/lint tooling in `pyproject.toml` optional dependencies and `requirements-dev.txt`.
3. Regenerate exact lock files for release builds after dependency changes.
4. Make Docker and CI install from those lock files.

## Frontend

`frontend-angular/package-lock.json` is the authoritative frontend install input for release builds.

Release and container builds must use `npm ci`, not `npm install`, so the install path follows the committed lockfile.

`package.json` version ranges are developer metadata until a stricter exact-version policy is chosen. A release build is not valid without the matching `package-lock.json`.

## Containers And Tools

Container base images, service images and globally installed tools must be pinned before the first v1.0.0 release.

The backend image pins `opencode-ai` via the `OPENCODE_AI_VERSION` build environment value. The current release track uses `opencode-ai@1.14.18` and verifies the installed CLI version during image build.

CI diagram rendering pins `@mermaid-js/mermaid-cli@11.12.0`.

Digest pinning is preferred for final release artifacts. If digest lookup is not available during development, use exact tags first and document the remaining drift in the release checklist.
