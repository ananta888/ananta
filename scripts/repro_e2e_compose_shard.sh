#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

shard_name="${1:-compose-foundation-auth}"
shift || true

case "$shard_name" in
  compose-foundation-auth)
    specs="tests/auth.spec.ts tests/auth-password.spec.ts tests/auth-lockout.spec.ts tests/auth-mfa.spec.ts tests/auth-rate-limit.spec.ts tests/permissions.spec.ts"
    ;;
  compose-foundation-ui)
    specs="tests/settings-config.spec.ts tests/llm-config.spec.ts tests/notifications.spec.ts tests/agents.spec.ts tests/terminal.spec.ts tests/lite-compose-connectivity.spec.ts"
    ;;
  compose-goal-core)
    specs="tests/main-goal-foundation.spec.ts tests/main-goal-planning.spec.ts tests/main-goal-execution.spec.ts tests/main-goal-observability.spec.ts tests/hub-flow.spec.ts"
    ;;
  compose-goal-smoke)
    specs="tests/first-goal-e2e.spec.ts tests/release-goal-smoke.spec.ts"
    ;;
  compose-admin-core)
    specs="tests/admin-core-journey.spec.ts tests/agent-registration.spec.ts tests/audit-logs.spec.ts tests/control-center-denied-flow.spec.ts tests/control-center-policies.spec.ts tests/control-center-review-flow.spec.ts tests/task-cleanup-ui.spec.ts"
    ;;
  compose-admin-entities)
    specs="tests/team-types-roles.spec.ts tests/teams.spec.ts tests/templates-crud.spec.ts"
    ;;
  compose-ai-interaction)
    specs="tests/ai-assistant-global-dock.spec.ts tests/ai-assistant-hybrid.spec.ts tests/ai-assistant-opencode.spec.ts tests/ai-assistant-settings-mutations.spec.ts tests/ai-snake-config-panel.spec.ts tests/ai-snake-session-sharing.spec.ts tests/auto-planner.spec.ts tests/config-ui-control-hardening.spec.ts tests/llm-generate.spec.ts tests/templates-ai.spec.ts"
    ;;
  compose-observability-network)
    specs="tests/a11y.spec.ts tests/live-click-critical-paths.spec.ts tests/public-oidc-webrtc.spec.ts tests/sse-events.spec.ts tests/stress.spec.ts tests/ui-ux-console.spec.ts tests/ui-ux-workflows.spec.ts tests/webrtc-datachannel.spec.ts"
    ;;
  *)
    echo "Unknown e2e compose shard: ${shard_name}" >&2
    exit 1
    ;;
esac

export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-test-postgres-password}"
export INITIAL_ADMIN_PASSWORD="${INITIAL_ADMIN_PASSWORD:-test123}"
export SECRET_KEY="${SECRET_KEY:-test-secret-key-with-at-least-thirty-two-chars}"
export AGENT_TOKEN_HUB="${AGENT_TOKEN_HUB:-hub-token}"
export AGENT_TOKEN_ALPHA="${AGENT_TOKEN_ALPHA:-alpha-token}"
export AGENT_TOKEN_BETA="${AGENT_TOKEN_BETA:-beta-token}"
export AGENT_TOKEN_GAMMA="${AGENT_TOKEN_GAMMA:-gamma-token}"
export AGENT_TOKEN_DELTA="${AGENT_TOKEN_DELTA:-delta-token}"
export GRAFANA_PASSWORD="${GRAFANA_PASSWORD:-test-grafana-password}"
export E2E_RESULTS_DIR="${E2E_RESULTS_DIR:-frontend-angular/test-results/${shard_name}}"
export E2E_LOGS_DIR="${E2E_LOGS_DIR:-ci-artifacts/e2e-compose/${shard_name}}"
export E2E_REPORTER_MODE="${E2E_REPORTER_MODE:-compact}"

docker compose -f docker/old_way/docker-compose.base.yml -f docker/old_way/docker-compose-lite.yml -f docker/old_way/docker-compose.github-ci.yml up -d --no-build \
  postgres redis ai-agent-hub ai-agent-alpha ai-agent-beta angular-frontend

cleanup() {
  docker compose -f docker/old_way/docker-compose.base.yml -f docker/old_way/docker-compose-lite.yml -f docker/old_way/docker-compose.github-ci.yml down -v --remove-orphans || true
}
trap cleanup EXIT

bash scripts/run_e2e_compose_shard.sh "$shard_name" "$specs"
