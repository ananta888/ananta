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

prepare_runtime_directories() {
  local runtime_file
  umask 077
  mkdir -p /app/data
  if [[ -n "${HOME:-}" ]]; then
    mkdir -p "${HOME}"
  fi
  if [[ -n "${XDG_CACHE_HOME:-}" ]]; then
    mkdir -p "${XDG_CACHE_HOME}"
  fi
  for runtime_file in /app/data/app.log /app/data/audit.log; do
    if [[ -L "$runtime_file" ]]; then
      error_exit "Refusing symlinked runtime log: ${runtime_file}"
    fi
    if [[ -e "$runtime_file" && ! -f "$runtime_file" ]]; then
      error_exit "Runtime log is not a regular file: ${runtime_file}"
    fi
    if [[ -f "$runtime_file" ]]; then
      chmod 0600 "$runtime_file"
    fi
  done
}

run_db_migrations_if_enabled() {
  local enabled
  enabled="$(normalize_lower "${ANANTA_RUN_DB_MIGRATIONS-1}")"
  case "${enabled}" in
    1|true|yes|on)
      log "Applying database migrations"
      alembic upgrade head
      ;;
    0|false|no|off)
      log "Skipping database migrations"
      ;;
    *)
      error_exit "ANANTA_RUN_DB_MIGRATIONS must be a boolean value."
      ;;
  esac
}

run_explicit_db_migrations_if_configured() {
  if [[ -v ANANTA_RUN_DB_MIGRATIONS ]]; then
    run_db_migrations_if_enabled
  fi
}

run_hub() {
  local agent_count
  require_openai_key_if_needed
  # ANANTA_AGENT_COUNT is a worker-container setting; a global value (e.g. a shared
  # .env) must never crash or fan out the control plane.
  agent_count="$(validate_agent_count)"
  if (( agent_count > 1 )); then
    log "ANANTA_AGENT_COUNT=${agent_count} applies to worker containers; the hub runs a single process."
  fi
  export ROLE=hub
  export PORT="${PORT:-5000}"
  log "Starting hub on port ${PORT}"
  prepare_runtime_directories
  run_db_migrations_if_enabled
  exec python -m agent.ai_agent
}

validate_agent_count() {
  local raw count
  raw="$(normalize_lower "${ANANTA_AGENT_COUNT:-1}")"
  if ! [[ "$raw" =~ ^[0-9]+$ ]]; then
    error_exit "ANANTA_AGENT_COUNT must be a positive integer (got '${ANANTA_AGENT_COUNT}')."
  fi
  count=$((10#$raw))
  if (( count < 1 || count > 16 )); then
    error_exit "ANANTA_AGENT_COUNT must be between 1 and 16 (got ${count})."
  fi
  printf '%s\n' "$count"
}

worker_port_for_index() {
  printf '%s\n' "$(($1 + $2 - 1))"
}

worker_name_for_index() {
  if (( $2 == 1 )); then
    printf '%s\n' "$1"
  else
    printf '%s\n' "$1-$2"
  fi
}

worker_url_for_index() {
  local url="$1" port="$2"
  if [[ "$url" =~ ^(https?://[^/:]+):[0-9]+$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}:${port}"
  else
    error_exit "AGENT_URL must end in ':<port>' when ANANTA_AGENT_COUNT > 1 (got '${url}')."
  fi
}

run_worker() {
  local count i port agent_name agent_url agent_data_dir exit_code
  local -a agent_env
  require_openai_key_if_needed
  count="$(validate_agent_count)"
  export ROLE=worker
  export PORT="${PORT:-5000}"
  if (( count == 1 )); then
    log "Starting worker '${AGENT_NAME:-worker}' on port ${PORT}"
    prepare_runtime_directories
    run_db_migrations_if_enabled
    exec python -m agent.ai_agent
  fi
  if [[ -z "${AGENT_URL:-}" ]]; then
    error_exit "AGENT_URL is required when ANANTA_AGENT_COUNT > 1 (it must end in ':<port>')."
  fi
  log "Starting ${count} worker agents on ports ${PORT}..$((PORT + count - 1))"
  prepare_runtime_directories
  run_db_migrations_if_enabled

  trap cleanup_children INT TERM
  for (( i = 1; i <= count; i++ )); do
    port="$(worker_port_for_index "$PORT" "$i")"
    agent_name="$(worker_name_for_index "${AGENT_NAME:-worker}" "$i")"
    agent_url="$(worker_url_for_index "$AGENT_URL" "$port")"
    agent_data_dir="${DATA_DIR:-data}/agent-${i}"
    mkdir -p "$agent_data_dir"
    agent_env=(
      ROLE=worker
      PORT="$port"
      AGENT_NAME="$agent_name"
      AGENT_URL="$agent_url"
      DATA_DIR="$agent_data_dir"
    )
    if [[ -n "${ANANTA_KNOWLEDGE_INDEX_WORKER_ID:-}" ]]; then
      agent_env+=(ANANTA_KNOWLEDGE_INDEX_WORKER_ID="$agent_name")
    fi
    start_prefixed "worker-${i}" env "${agent_env[@]}" python -m agent.ai_agent
  done

  wait -n "${CHILD_PIDS[@]}"
  exit_code=$?
  cleanup_children
  wait || true
  exit "$exit_code"
}

run_frontend() {
  local frontend_port frontend_poll disable_host_check
  local -a command
  umask 077
  if [[ -n "${HOME:-}" ]]; then
    mkdir -p "${HOME}"
  fi
  if [[ -n "${XDG_CACHE_HOME:-}" ]]; then
    mkdir -p "${XDG_CACHE_HOME}"
  fi
  frontend_port="${FRONTEND_PORT:-4200}"
  frontend_poll="${FRONTEND_POLL_MS:-2000}"
  disable_host_check="$(normalize_lower "${ANANTA_FRONTEND_DISABLE_HOST_CHECK:-1}")"
  log "Starting frontend on port ${frontend_port}"
  cd /app/frontend-angular
  command=(npx ng serve --host 0.0.0.0 --port "${frontend_port}" --poll "${frontend_poll}")
  case "${disable_host_check}" in
    0|false|no|off) ;;
    1|true|yes|on) command+=(--disable-host-check) ;;
    *) error_exit "ANANTA_FRONTEND_DISABLE_HOST_CHECK must be a boolean value." ;;
  esac
  exec "${command[@]}"
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
  run_explicit_db_migrations_if_configured
  if [[ "$#" -gt 0 ]]; then
    log "Agent-only passthrough command: $*"
    exec "$@"
  fi
  log "Agent-only fallback command (hub bootstrap)"
  prepare_runtime_directories
  if [[ ! -v ANANTA_RUN_DB_MIGRATIONS ]]; then
    run_db_migrations_if_enabled
  fi
  exec python -m agent.ai_agent
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
  prepare_runtime_directories
  run_explicit_db_migrations_if_configured

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

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
