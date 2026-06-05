#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

compose_files=(
  -f docker-compose.base.yml
  -f docker-compose-lite.yml
  -f docker-compose.github-ci.yml
)

results_root="frontend-angular/test-results"
logs_root="ci-artifacts/e2e-compose"
mkdir -p "$results_root" "$logs_root"

groups=(
  "compose-foundation|tests/auth.spec.ts tests/auth-password.spec.ts tests/auth-lockout.spec.ts tests/auth-mfa.spec.ts tests/auth-rate-limit.spec.ts tests/permissions.spec.ts tests/settings-config.spec.ts tests/llm-config.spec.ts tests/notifications.spec.ts tests/agents.spec.ts tests/terminal.spec.ts tests/lite-compose-connectivity.spec.ts"
  "compose-goal-flow|tests/main-goal-foundation.spec.ts tests/main-goal-planning.spec.ts tests/main-goal-execution.spec.ts tests/main-goal-observability.spec.ts tests/hub-flow.spec.ts tests/first-goal-e2e.spec.ts tests/release-goal-smoke.spec.ts"
  "compose-admin-control|tests/admin-core-journey.spec.ts tests/agent-registration.spec.ts tests/audit-logs.spec.ts tests/control-center-denied-flow.spec.ts tests/control-center-policies.spec.ts tests/control-center-review-flow.spec.ts tests/task-cleanup-ui.spec.ts tests/team-types-roles.spec.ts tests/teams.spec.ts tests/templates-crud.spec.ts"
  "compose-ai-llm|tests/ai-assistant-global-dock.spec.ts tests/ai-assistant-hybrid.spec.ts tests/ai-assistant-opencode.spec.ts tests/ai-assistant-settings-mutations.spec.ts tests/ai-snake-config-panel.spec.ts tests/ai-snake-session-sharing.spec.ts tests/auto-planner.spec.ts tests/config-ui-control-hardening.spec.ts tests/llm-generate.spec.ts tests/templates-ai.spec.ts"
  "compose-observability-network|tests/a11y.spec.ts tests/live-click-critical-paths.spec.ts tests/public-oidc-webrtc.spec.ts tests/sse-events.spec.ts tests/stress.spec.ts tests/ui-ux-console.spec.ts tests/ui-ux-workflows.spec.ts tests/webrtc-datachannel.spec.ts"
)

declare -a shard_pids=()
declare -a shard_names=()
declare -a shard_logs=()

run_shard() {
  local shard_name="$1"
  local spec_string="$2"
  local log_file="$3"
  local seed_team_name="E2E Seed Scrum Team ${shard_name}"

  (
    set -euo pipefail
    read -r -a specs <<< "$spec_string"
    docker compose "${compose_files[@]}" run --rm --no-deps \
      -e E2E_DETERMINISTIC_SCRUM_SEED=1 \
      -e E2E_SCRUM_SEED_TEAM_NAME="${seed_team_name}" \
      -e E2E_RESULTS_DIR="/app/test-results/${shard_name}" \
      frontend-test \
      npm run test:e2e:compose -- "${specs[@]}"
  ) >"$log_file" 2>&1
}

for group in "${groups[@]}"; do
  shard_name="${group%%|*}"
  spec_string="${group#*|}"
  log_file="${logs_root}/${shard_name}.log"

  printf '[e2e-compose] starting %s\n' "$shard_name"
  run_shard "$shard_name" "$spec_string" "$log_file" &
  shard_pids+=("$!")
  shard_names+=("$shard_name")
  shard_logs+=("$log_file")
done

overall_status=0
for idx in "${!shard_pids[@]}"; do
  pid="${shard_pids[$idx]}"
  shard_name="${shard_names[$idx]}"
  log_file="${shard_logs[$idx]}"

  if wait "$pid"; then
    printf '[e2e-compose] shard %s passed\n' "$shard_name"
    continue
  fi

  shard_status=$?
  overall_status=1
  printf '[e2e-compose] shard %s failed with exit code %s\n' "$shard_name" "$shard_status"
  printf '[e2e-compose] tail of %s\n' "$log_file"
  tail -n 200 "$log_file" || true
done

exit "$overall_status"
