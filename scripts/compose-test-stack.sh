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
  scripts/compose-test-stack.sh clean
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

Safety:
  - 'down' keeps Docker volumes (including ollama_data).
  - 'clean' removes test-stack volumes except ollama_data.
EOF
}

remove_non_ollama_volumes() {
  local project
  project="${COMPOSE_PROJECT_NAME:-$(basename "$ROOT_DIR")}"

  mapfile -t volume_keys < <("${compose_cmd[@]}" config --volumes 2>/dev/null || true)
  if [[ ${#volume_keys[@]} -eq 0 ]]; then
    echo "No compose volumes declared; skipping volume cleanup."
    return 0
  fi

  local deleted=0
  local skipped=0
  for key in "${volume_keys[@]}"; do
    [[ -z "$key" ]] && continue
    if [[ "$key" == "ollama_data" || "$key" == *"ollama_data" ]]; then
      echo "Keeping protected volume key: $key"
      ((skipped += 1))
      continue
    fi

    # Try compose-prefixed and plain volume names (for possible custom/external naming).
    local candidate_names=("${project}_${key}" "$key")
    local removed_this_key=0
    for volume_name in "${candidate_names[@]}"; do
      if docker volume inspect "$volume_name" >/dev/null 2>&1; then
        if docker volume rm "$volume_name" >/dev/null 2>&1; then
          echo "Removed volume: $volume_name"
          removed_this_key=1
          ((deleted += 1))
        else
          echo "Could not remove volume (maybe still in use): $volume_name"
        fi
      fi
    done

    if [[ "$removed_this_key" -eq 0 ]]; then
      echo "Volume not present for key: $key"
    fi
  done

  echo "Cleanup summary: removed=$deleted, protected=$skipped"
}

cmd="${1:-}"
shift || true

case "$cmd" in
  up)
    "${compose_cmd[@]}" up -d --build
    ;;
  down)
    "${compose_cmd[@]}" down --remove-orphans
    ;;
  clean)
    "${compose_cmd[@]}" down --remove-orphans
    remove_non_ollama_volumes
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
