#!/usr/bin/env bash
# Canonical FULL backend test run — all tests, in the pinned Docker image (~15 min).
#
# This is THE way to run the complete backend suite. Do not run a bare
# single-process `pytest tests/` for full-suite validation on a dev machine:
# the host environment lacks pinned deps (alembic, playwright, hypothesis, ...)
# and one process takes 35+ minutes. The test image installs the exact
# lockfiles and pytest runs with xdist (-n 4), which is what CI does.
#
# Usage:
#   scripts/test_full_docker.sh                 # all tiers, full suite
#   scripts/test_full_docker.sh --tier core     # fast tier only
#   scripts/test_full_docker.sh tests/task/     # subset (paths/args passthrough)
#   scripts/test_full_docker.sh --build         # force image rebuild first
#
# Extra arguments are forwarded to pytest unchanged.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

IMAGE="${ANANTA_TEST_IMAGE:-ananta-backend-tests:local}"
DOCKERFILE="docker/compose-next/Dockerfile.tests-backend"

FORCE_BUILD=0
PYTEST_ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--build" ]]; then
    FORCE_BUILD=1
  else
    PYTEST_ARGS+=("$arg")
  fi
done

if [[ "$FORCE_BUILD" == "1" ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[test_full_docker] building $IMAGE from $DOCKERFILE ..."
  docker build -f "$DOCKERFILE" -t "$IMAGE" .
fi

# Environment mirrors docker/compose-next/compose.tests-backend-base.yml.
# The suite is hermetic (in-memory SQLite sandbox in tests/conftest.py);
# DEFAULT_PROVIDER=mock keeps LLM-touching tests deterministic.
echo "[test_full_docker] running full suite in $IMAGE (pytest -n 4) ..."
exec docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -e PYTHONPATH=/app \
  -e PYTHONUNBUFFERED=1 \
  -e RUFF_CACHE_DIR=/tmp/ruff-cache \
  -e ANANTA_QUICKSTART_MODE=agent-only \
  -e DEFAULT_PROVIDER=mock \
  -e DEFAULT_MODEL=mock \
  -v "$ROOT_DIR":/app \
  -w /app \
  "$IMAGE" \
  python -m pytest -q -p no:cacheprovider -o addopts='' -n 4 --timeout=300 --tier all --durations=15 "${PYTEST_ARGS[@]}"
