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

Top-level `dependencies` and `devDependencies` in `frontend-angular/package.json` must use exact versions for release branches. Version range updates are allowed during dependency maintenance, but the final release branch must commit the resulting exact package manifest and matching `package-lock.json`.

## Containers And Tools

Container base images, service images and globally installed tools must be pinned before the first v1.0.0 release.

The backend image pins `opencode-ai` via the `OPENCODE_AI_VERSION` build environment value. The current release track uses `opencode-ai@1.14.18` and verifies the installed CLI version during image build.

CI diagram rendering pins `@mermaid-js/mermaid-cli@11.12.0`.

Release images must use explicit tags plus registry digests where the registry is public and the digest can be resolved. Local images such as `ananta-backend-compose-test:local` and `ollama-wsl-amd:0.20.7-vulkan` are build outputs and are not registry-digest pinned.

## Apt Snapshots

Backend images are pinned to `python:3.11.15-slim-bookworm`, which fixes both the Python patch line and Debian suite for the application runtime.

Backend apt packages resolve through Debian snapshot `20260406T000000Z`. The WSL/Vulkan Ollama build path resolves Ubuntu packages through Ubuntu snapshot `20260406T000000Z` and does not add moving PPAs.

This removes the previously documented apt rest drift from the v1.0.0 release path.
