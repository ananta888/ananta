#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[quickstart-entrypoint] %s\n' "$*"
}

error_exit() {
  printf '[quickstart-entrypoint] ERROR: %s\n' "$*" >&2
  exit 64
}

normalize_lower() {
  printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]'
}

require_openai_key_if_needed() {
  local provider
  provider="$(normalize_lower "${DEFAULT_PROVIDER:-}")"
  if [[ "$provider" != "openai" ]]; then
    return
  fi
  if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    error_exit "DEFAULT_PROVIDER=openai requires OPENAI_API_KEY. Configure OPENAI_API_KEY (or switch DEFAULT_PROVIDER)."
  fi
}

run_hub() {
  require_openai_key_if_needed
  export ROLE=hub
  export PORT="${PORT:-5000}"
  log "Starting hub on port ${PORT}"
  alembic upgrade head
  exec python -m agent.ai_agent
}

run_worker() {
  require_openai_key_if_needed
  export ROLE=worker
  export PORT="${PORT:-5000}"
  log "Starting worker '${AGENT_NAME:-worker}' on port ${PORT}"
  exec python -m agent.ai_agent
}

run_frontend() {
  local frontend_port frontend_poll
  frontend_port="${FRONTEND_PORT:-4200}"
  frontend_poll="${FRONTEND_POLL_MS:-2000}"
  log "Starting frontend on port ${frontend_port}"
  cd /app/frontend-angular
  exec npx ng serve --host 0.0.0.0 --port "${frontend_port}" --poll "${frontend_poll}" --disable-host-check
}

run_evolver_bridge() {
  export PORT="${PORT:-8080}"
  log "Starting evolver bridge on port ${PORT}"
  exec node /app/services/evolver_bridge/server.js
}

deerflow_preflight() {
  local workdir command_template
  workdir="${DEERFLOW_WORKING_DIR:-}"
  command_template="${DEERFLOW_COMMAND:-python main.py {prompt}}"
  if [[ -z "${workdir}" || ! -d "${workdir}" ]]; then
    printf '[deerflow] preflight_error: DEERFLOW_WORKING_DIR missing or not found (%s)\n' "${workdir:-<empty>}" >&2
  fi
  if [[ -z "${command_template}" ]]; then
    printf '[deerflow] preflight_error: DEERFLOW_COMMAND is empty\n' >&2
  fi
}

ml_intern_preflight() {
  local template
  template="${ML_INTERN_COMMAND_TEMPLATE:-}"
  if [[ -z "${template}" ]]; then
    printf '[ml_intern] preflight_error: ML_INTERN_COMMAND_TEMPLATE missing (ml_intern_spike remains disabled)\n' >&2
  fi
}

run_deerflow_runner() {
  deerflow_preflight
  export AGENT_NAME="${AGENT_NAME:-deerflow-runner}"
  export HUB_URL="${HUB_URL:-http://ai-agent-hub:5000}"
  run_worker
}

run_ml_intern_runner() {
  ml_intern_preflight
  export AGENT_NAME="${AGENT_NAME:-ml-intern-runner}"
  export HUB_URL="${HUB_URL:-http://ai-agent-hub:5000}"
  run_worker
}

run_agent_only() {
  require_openai_key_if_needed
  if [[ "$#" -gt 0 ]]; then
    log "Agent-only passthrough command: $*"
    exec "$@"
  fi
  log "Agent-only fallback command (hub bootstrap)"
  exec sh -c "alembic upgrade head && exec python -m agent.ai_agent"
}

declare -a CHILD_PIDS=()

start_prefixed() {
  local prefix="$1"
  shift
  (
    "$@" 2>&1 | sed -u "s/^/[${prefix}] /"
  ) &
  CHILD_PIDS+=("$!")
}

cleanup_children() {
  local pid
  for pid in "${CHILD_PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}

run_single_container() {
  local hub_port worker_port frontend_port frontend_poll
  hub_port="${HUB_PORT:-5000}"
  worker_port="${WORKER_PORT:-5001}"
  frontend_port="${FRONTEND_PORT:-4200}"
  frontend_poll="${FRONTEND_POLL_MS:-2000}"
  require_openai_key_if_needed

  trap cleanup_children INT TERM

  start_prefixed "hub" env ROLE=hub PORT="${hub_port}" HUB_CAN_BE_WORKER="${HUB_CAN_BE_WORKER:-true}" python -m agent.ai_agent
  start_prefixed "worker" env ROLE=worker AGENT_NAME="${WORKER_AGENT_NAME:-local-worker}" PORT="${worker_port}" HUB_URL="http://127.0.0.1:${hub_port}" AGENT_URL="http://127.0.0.1:${worker_port}" python -m agent.ai_agent
  start_prefixed "frontend" bash -lc "cd /app/frontend-angular && exec npx ng serve --host 0.0.0.0 --port ${frontend_port} --poll ${frontend_poll} --disable-host-check"

  wait -n "${CHILD_PIDS[@]}"
  local exit_code=$?
  cleanup_children
  wait || true
  exit "$exit_code"
}

main() {
  local mode role
  mode="$(normalize_lower "${ANANTA_QUICKSTART_MODE:-role}")"
  role="$(normalize_lower "${ANANTA_QUICKSTART_ROLE:-hub}")"

  case "$mode" in
    agent-only)
      run_agent_only "$@"
      ;;
    single-container)
      run_single_container
      ;;
    role)
      case "$role" in
        hub) run_hub ;;
        worker) run_worker ;;
        frontend) run_frontend ;;
        evolver_bridge) run_evolver_bridge ;;
        deerflow_runner) run_deerflow_runner ;;
        ml_intern_runner) run_ml_intern_runner ;;
        *)
          error_exit "Unknown ANANTA_QUICKSTART_ROLE='${role}'. Allowed: hub, worker, frontend, evolver_bridge, deerflow_runner, ml_intern_runner."
          ;;
      esac
      ;;
    *)
      error_exit "Unknown ANANTA_QUICKSTART_MODE='${mode}'. Allowed: role, agent-only, single-container."
      ;;
  esac
}

main "$@"
