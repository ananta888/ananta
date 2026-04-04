#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Default: WSL2/Vulkan overlay enabled for test/e2e stack.
USE_WSL_VULKAN="${ANANTA_USE_WSL_VULKAN:-1}"

compose_files=(
  "docker-compose.base.yml"
  "docker-compose-lite.yml"
)

if [[ "$USE_WSL_VULKAN" == "1" ]]; then
  compose_files+=("docker-compose.ollama-wsl.yml")
fi

compose_files+=("docker-compose.test.yml")

compose_cmd=(docker compose)
for file in "${compose_files[@]}"; do
  compose_cmd+=(-f "$file")
done

usage() {
  cat <<'EOF'
Usage:
  scripts/compose-test-stack.sh up
  scripts/compose-test-stack.sh down
  scripts/compose-test-stack.sh ps
  scripts/compose-test-stack.sh config
  scripts/compose-test-stack.sh run <service> [args...]
  scripts/compose-test-stack.sh run-backend-test [args...]
  scripts/compose-test-stack.sh run-backend-live-llm-test [args...]
  scripts/compose-test-stack.sh run-frontend-test [args...]
  scripts/compose-test-stack.sh run-frontend-live-llm-test [args...]

Env:
  ANANTA_USE_WSL_VULKAN=1   Default. Includes docker-compose.ollama-wsl.yml.
  ANANTA_USE_WSL_VULKAN=0   Disable WSL2/Vulkan overlay.
EOF
}

cmd="${1:-}"
shift || true

case "$cmd" in
  up)
    "${compose_cmd[@]}" up -d --build
    ;;
  down)
    "${compose_cmd[@]}" down -v --remove-orphans
    ;;
  ps)
    "${compose_cmd[@]}" ps
    ;;
  config)
    "${compose_cmd[@]}" config
    ;;
  run)
    if [[ $# -lt 1 ]]; then
      echo "run requires <service>" >&2
      usage
      exit 2
    fi
    svc="$1"
    shift
    "${compose_cmd[@]}" run --rm "$svc" "$@"
    ;;
  run-backend-test)
    "${compose_cmd[@]}" run --rm backend-test "$@"
    ;;
  run-backend-live-llm-test)
    "${compose_cmd[@]}" run --rm backend-live-llm-test "$@"
    ;;
  run-frontend-test)
    "${compose_cmd[@]}" run --rm frontend-test "$@"
    ;;
  run-frontend-live-llm-test)
    "${compose_cmd[@]}" run --rm frontend-live-llm-test "$@"
    ;;
  ""|-h|--help|help)
    usage
    ;;
  *)
    echo "Unknown command: $cmd" >&2
    usage
    exit 2
    ;;
esac
