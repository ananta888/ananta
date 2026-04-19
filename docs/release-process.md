# Ananta Release Process

This document defines the v1.0.0 release process for Ananta.

## Release Gate

Every release candidate must pass the release gate from a clean checkout:

```bash
ANANTA_DOCKER_CLEAN_PATH=1 \
ANANTA_NPM_COMMAND="npx -p node@20.19.5 node /usr/bin/npm" \
python scripts/release_gate.py \
  --compose-config \
  --frontend-build \
  --build-images \
  --report release-verification-report.json
```

The gate verifies locked Python dependencies, exact frontend package versions, digest-pinned images, pinned GitHub Actions, exact CI runtimes, fixed apt snapshots, Compose rendering, frontend build output and backend/frontend image builds.

## Standard Checks

Run the normal quality pipeline before the release gate:

```bash
make check
```

For a larger pre-release sweep:

```bash
make check-deep
```

## Docker Images

Release builds use digest-pinned base images from `Dockerfile` and `frontend-angular/Dockerfile`.

Build the release candidate images with explicit candidate tags:

```bash
docker build -t ananta-backend:v1.0.0-rc .
docker build -t ananta-frontend:v1.0.0-rc frontend-angular
```

The release gate already executes equivalent backend and frontend image builds with `:release-gate` tags.

## Smoke Test

Use the release gate as the smoke test for the build path. It verifies that the backend and frontend images build from the pinned inputs and that the frontend production build completes with Node `20.19.5`.

For runtime smoke testing, start the pinned lite stack:

```bash
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml up -d --build
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml ps
docker compose -f docker-compose.base.yml -f docker-compose-lite.yml down -v --remove-orphans
```

## Versioning

The Python package version is maintained in `pyproject.toml`.

Release candidate evidence must include:

```bash
git rev-parse HEAD
python --version
node --version
npm --version
docker --version
docker compose version
python scripts/release_gate.py --compose-config --report release-verification-report.json
```

## CI

Release-relevant GitHub Actions are pinned to commit SHAs, not floating major tags. CI uses Python `3.11.15`, Node `20.19.5`, Python lockfiles and `npm ci`.

The `release-gate` CI job uploads `release-verification-report.json`. A failed release gate blocks the release.
