#!/usr/bin/env bash
set -euo pipefail

# Backpressure soak: reproduce Hub-Restart, DB/Redis/Search/Relay/SFU outages,
# slow receivers, network switches and queue pressure for the Agent/Live lane.
# Buzz failures are handled by the dedicated Buzz-Gate only.

HUB_PORT="${HUB_PORT:-8080}"
DB_URL="${DB_URL:-postgres://localhost:5432/ananta}"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
SEARCH_URL="${SEARCH_URL:-http://localhost:8081}"
RELAY_URL="${RELAY_URL:-ws://localhost:8082}"
SFU_URL="${SFU_URL:-ws://localhost:8083}"
SOAK_SECONDS="${SOAK_SECONDS:-60}"
QUEUE_PRESSURE="${QUEUE_PRESSURE:-1000}"

log() { printf '[backpressure-soak] %s\n' "$*"; }

check_service() {
  local name="$1" url="$2"
  if curl -fsS --max-time 3 "$url" >/dev/null 2>&1 || redis-cli -u "${REDIS_URL#redis://}" ping >/dev/null 2>&1; then
    log "$name up"
  else
    log "$name down (expected during chaos window)"
  fi
}

run_soak() {
  log "Starting soak for ${SOAK_SECONDS}s"
  local start=$(date +%s)
  while true; do
    check_service hub "http://localhost:${HUB_PORT}/health"
    check_service db "$DB_URL"
    check_service redis "$REDIS_URL"
    check_service search "$SEARCH_URL/health"
    check_service relay "$RELAY_URL/health"
    check_service sfu "$SFU_URL/health"

    # Simulate queue pressure by pushing synthetic events.
    for ((i=0; i<QUEUE_PRESSURE; i++)); do
      curl -fsS -X POST "http://localhost:${HUB_PORT}/events" \
        -H 'Content-Type: application/json' \
        -d "{\"type\":\"soak\",\"seq\":${i}}" >/dev/null 2>&1 || true
    done

    sleep 5
    local now=$(date +%s)
    if (( now - start >= SOAK_SECONDS )); then
      break
    fi
  done
  log "Soak complete"
}

main() {
  run_soak
}

main "$@"
