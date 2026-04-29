#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUTO_SHUTDOWN_STACK="${ANANTA_ANDROID_AUTO_SHUTDOWN_STACK:-1}"

prepare_docker_config() {
  local src="${DOCKER_CONFIG:-$HOME/.docker}"
  export DOCKER_CONFIG="${ANANTA_DOCKER_CONFIG_DIR:-/tmp/ananta-docker-config}"
  mkdir -p "$DOCKER_CONFIG"

  if [[ -f "$src/config.json" ]]; then
    cp "$src/config.json" "$DOCKER_CONFIG/config.json"
  else
    echo '{}' > "$DOCKER_CONFIG/config.json"
  fi

  # WSL/headless-safe: disable helpers that require secretservice/dbus.
  python3 - "$DOCKER_CONFIG/config.json" <<'PY'
import json
import sys
p = sys.argv[1]
with open(p, 'r', encoding='utf-8') as f:
    data = json.load(f)
data.pop('credsStore', None)
data.pop('credHelpers', None)
with open(p, 'w', encoding='utf-8') as f:
    json.dump(data, f)
PY
}

start_stack() {
  "$ROOT_DIR/scripts/compose-test-stack.sh" up-live
}

stop_stack() {
  "$ROOT_DIR/scripts/compose-test-stack.sh" down || true
}

main() {
  prepare_docker_config
  trap 'if [[ "$AUTO_SHUTDOWN_STACK" == "1" ]]; then stop_stack; fi' EXIT

  start_stack
  "$ROOT_DIR/scripts/compose-test-stack.sh" run-android-e2e
}

main "$@"
