#!/usr/bin/env bash
set +e

OUT_DIR="ci-artifacts/e2e-compose"
mkdir -p "${OUT_DIR}"

COMPOSE_FILES=(
  -f docker-compose.base.yml
  -f docker-compose-lite.yml
  -f docker-compose.github-ci.yml
)

run_and_capture() {
  local name="$1"
  shift
  echo "[e2e-diagnostics] $*" | tee "${OUT_DIR}/${name}.txt"
  "$@" >> "${OUT_DIR}/${name}.txt" 2>&1 || true
}

run_and_capture docker-ps-a docker ps -a
run_and_capture docker-images docker images
run_and_capture compose-ps docker compose "${COMPOSE_FILES[@]}" ps
run_and_capture compose-config docker compose "${COMPOSE_FILES[@]}" config
run_and_capture compose-logs docker compose "${COMPOSE_FILES[@]}" logs --no-color

for service in postgres redis ai-agent-hub ai-agent-alpha ai-agent-beta angular-frontend frontend-test; do
  run_and_capture "${service}-logs" docker compose "${COMPOSE_FILES[@]}" logs --no-color "${service}"
done

if [ -d frontend-angular/test-results ]; then
  mkdir -p "${OUT_DIR}/frontend-test-results"
  cp -R frontend-angular/test-results/. "${OUT_DIR}/frontend-test-results/" || true
fi

if [ -f frontend-angular/test-results/failure-summary.md ]; then
  cp frontend-angular/test-results/failure-summary.md "${OUT_DIR}/failure-summary.md" || true
fi

# Best-effort health snapshots from the GitHub runner network.
# These may fail when services are only reachable inside the compose network.
{
  echo "# Health snapshots"
  echo
  for url in \
    http://localhost:4200 \
    http://localhost:5000/health \
    http://localhost:5001/health \
    http://localhost:5002/health; do
    echo "## ${url}"
    curl -fsS --max-time 5 "${url}" || true
    echo
  done
} > "${OUT_DIR}/health-snapshots.txt" 2>&1

echo "[e2e-diagnostics] collected files:"
find "${OUT_DIR}" -maxdepth 3 -type f -print | sort
