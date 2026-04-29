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

prepare_docker_credential_stub() {
  local helper_dir="${ANANTA_DOCKER_HELPER_DIR:-/tmp/ananta-docker-helpers}"
  mkdir -p "$helper_dir"
  cat > "$helper_dir/docker-credential-secretservice" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
case "$cmd" in
  get)
    echo '{"Username":"","Secret":""}'
    ;;
  list)
    echo '{}'
    ;;
  store|erase)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
SCRIPT
  chmod +x "$helper_dir/docker-credential-secretservice"
  export PATH="$helper_dir:$PATH"
}

start_stack() {
  export ANANTA_USE_WSL_VULKAN="${ANANTA_USE_WSL_VULKAN:-0}"
  "$ROOT_DIR/scripts/compose-test-stack.sh" down || true
  "$ROOT_DIR/scripts/compose-test-stack.sh" up-live
}

stop_stack() {
  "$ROOT_DIR/scripts/compose-test-stack.sh" down || true
}

main() {
  prepare_docker_config
  prepare_docker_credential_stub
  trap 'if [[ "$AUTO_SHUTDOWN_STACK" == "1" ]]; then stop_stack; fi' EXIT

  start_stack
  "$ROOT_DIR/scripts/compose-test-stack.sh" run-android-e2e
}

main "$@"
