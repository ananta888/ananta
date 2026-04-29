#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUTO_SHUTDOWN_STACK="${ANANTA_ANDROID_AUTO_SHUTDOWN_STACK:-1}"

prepare_docker_config() {
  export DOCKER_CONFIG="${ANANTA_DOCKER_CONFIG_DIR:-/tmp/ananta-docker-config}"
  export DOCKER_AUTH_CONFIG='{"auths":{}}'

  # BuildKit is more resilient for large contexts on WSL mounts.
  export DOCKER_BUILDKIT=1
  export COMPOSE_DOCKER_CLI_BUILD=1

  mkdir -p "$DOCKER_CONFIG"
  cat > "$DOCKER_CONFIG/config.json" <<'JSON'
{
  "auths": {}
}
JSON
}

start_stack() {
  export ANANTA_USE_WSL_VULKAN="${ANANTA_USE_WSL_VULKAN:-0}"
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
