#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-ananta-firefox-vnc}"
IMAGE="${IMAGE:-selenium/standalone-firefox:latest}"
NETWORK_NAME="${NETWORK_NAME:-ananta_default}"
VNC_PORT="${VNC_PORT:-7900}"
SELENIUM_PORT="${SELENIUM_PORT:-4444}"
SHM_SIZE="${SHM_SIZE:-2g}"

usage() {
  cat <<'EOF'
Usage:
  scripts/start-firefox-vnc.sh start
  scripts/start-firefox-vnc.sh stop
  scripts/start-firefox-vnc.sh restart
  scripts/start-firefox-vnc.sh status

Env overrides:
  CONTAINER_NAME   (default: ananta-firefox-vnc)
  IMAGE            (default: selenium/standalone-firefox:latest)
  NETWORK_NAME     (default: ananta_default)
  VNC_PORT         (default: 7900)
  SELENIUM_PORT    (default: 4444)
  SHM_SIZE         (default: 2g)

Notes:
  - Browser URL from Windows/Host: http://localhost:<VNC_PORT>
  - WebDriver URL from Host:      http://localhost:<SELENIUM_PORT>/wd/hub
  - Inside browser use: http://angular-frontend:4200
EOF
}

ensure_network() {
  if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    echo "ERROR: Docker network '$NETWORK_NAME' not found." >&2
    echo "Hint: Start test stack first:" >&2
    echo "  docker compose -f docker-compose.base.yml -f docker-compose-lite.yml -f docker-compose.test.yml up -d --build" >&2
    exit 1
  fi
}

start_container() {
  ensure_network
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  docker run -d \
    --name "$CONTAINER_NAME" \
    --network "$NETWORK_NAME" \
    -p "${VNC_PORT}:7900" \
    -p "${SELENIUM_PORT}:4444" \
    --shm-size "$SHM_SIZE" \
    "$IMAGE" >/dev/null
  echo "Started $CONTAINER_NAME on http://localhost:${VNC_PORT}"
  echo "WebDriver endpoint: http://localhost:${SELENIUM_PORT}/wd/hub"
  echo "Open inside browser: http://angular-frontend:4200"
}

stop_container() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  echo "Stopped $CONTAINER_NAME"
}

status_container() {
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | awk 'NR==1 || $1=="'"$CONTAINER_NAME"'"'
}

cmd="${1:-start}"
case "$cmd" in
  start) start_container ;;
  stop) stop_container ;;
  restart) stop_container; start_container ;;
  status) status_container ;;
  -h|--help|help) usage ;;
  *)
    usage
    exit 1
    ;;
esac
