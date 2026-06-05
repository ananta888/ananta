#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

shard_name="${1:?missing shard name}"
spec_string="${2:?missing spec string}"

compose_files=(
  -f docker-compose.base.yml
  -f docker-compose-lite.yml
  -f docker-compose.github-ci.yml
)

results_root="${E2E_RESULTS_DIR:-frontend-angular/test-results/${shard_name}}"
logs_root="${E2E_LOGS_DIR:-ci-artifacts/e2e-compose/${shard_name}}"
log_file="${logs_root}/${shard_name}.log"
seed_team_name="E2E Seed Scrum Team ${shard_name}"

mkdir -p "$results_root" "$logs_root"

read -r -a specs <<< "$spec_string"
if [[ "${#specs[@]}" -eq 0 ]]; then
  echo "[e2e-compose] no specs supplied for ${shard_name}" >&2
  exit 1
fi

echo "[e2e-compose] running ${shard_name} with ${#specs[@]} specs"
if docker compose "${compose_files[@]}" run --rm --no-deps \
  -e E2E_DETERMINISTIC_SCRUM_SEED=1 \
  -e E2E_SCRUM_SEED_TEAM_NAME="${seed_team_name}" \
  -e E2E_RESULTS_DIR="/app/test-results/${shard_name}" \
  frontend-test \
  npm run test:e2e:compose -- "${specs[@]}" >"$log_file" 2>&1; then
  echo "[e2e-compose] shard ${shard_name} passed"
else
  status=$?
  echo "[e2e-compose] shard ${shard_name} failed with exit code ${status}" >&2
  echo "[e2e-compose] tail of ${log_file}" >&2
  tail -n 200 "$log_file" >&2 || true
  exit "$status"
fi
