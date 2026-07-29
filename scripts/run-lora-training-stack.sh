#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 lora-training-{mock|cpu|nvidia} [docker compose command...]" >&2
  exit 64
}

profile="${1:-}"
[[ -n "$profile" ]] || usage
shift

case "$profile" in
  lora-training-mock)
    mode=dry_run
    default_backend=mock
    worker_backends=mock
    resource_profile=mock
    gpu_profile=none
    ;;
  lora-training-cpu)
    mode=live
    default_backend=peft_trl
    worker_backends=peft_trl
    resource_profile=cpu
    gpu_profile=none
    ;;
  lora-training-nvidia)
    mode=live
    default_backend=peft_trl
    worker_backends=peft_trl,unsloth,unsloth_vision,unsloth_audio,unsloth_embedding
    resource_profile=nvidia
    gpu_profile=rtx3080-safe
    ;;
  *) usage ;;
esac

if [[ "${1:-}" == "--print-profile" ]]; then
  printf 'profile=%s\nmode=%s\ndefault_backend=%s\nworker_backends=%s\nresource_profile=%s\ngpu_profile=%s\n' \
    "$profile" "$mode" "$default_backend" "$worker_backends" "$resource_profile" "$gpu_profile"
  exit 0
fi

: "${ANANTA_LORA_TRAINING_INTERNAL_TOKEN:?set a random training service token with at least 24 characters}"
: "${ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON:?set the local model catalog with relative_path and snapshot_hash}"
: "${ANANTA_LORA_TRAINING_MODEL_DIR:?set the host directory containing local model snapshots}"
if (( ${#ANANTA_LORA_TRAINING_INTERNAL_TOKEN} < 24 )); then
  echo "ANANTA_LORA_TRAINING_INTERNAL_TOKEN must contain at least 24 characters" >&2
  exit 64
fi
if [[ ! -d "$ANANTA_LORA_TRAINING_MODEL_DIR" ]]; then
  echo "ANANTA_LORA_TRAINING_MODEL_DIR is not a directory: $ANANTA_LORA_TRAINING_MODEL_DIR" >&2
  exit 66
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
"${PYTHON:-python3}" -c \
  'import json, os; value=json.loads(os.environ["ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON"]); assert isinstance(value, dict) and value' \
  || { echo "ANANTA_LORA_TRAINING_MODEL_CATALOG_JSON must be a non-empty JSON object" >&2; exit 64; }

export ANANTA_LORA_TRAINING_MODE="$mode"
export ANANTA_LORA_TRAINING_DEFAULT_BACKEND="$default_backend"
export ANANTA_LORA_TRAINING_WORKER_BACKENDS="$worker_backends"
export ANANTA_LORA_TRAINING_RESOURCE_PROFILE="$resource_profile"
export ANANTA_LORA_TRAINING_GPU_PROFILE="$gpu_profile"

base_compose="${ANANTA_LORA_TRAINING_BASE_COMPOSE:-docker/compose-next/compose.stack.full.yml}"
env_file="${ANANTA_LORA_TRAINING_ENV_FILE:-.env}"
compose_args=(-f "$base_compose" -f docker/compose-next/compose.lora-training.yml --profile "$profile")
if [[ -f "$env_file" ]]; then
  compose_args=(--env-file "$env_file" "${compose_args[@]}")
fi
if (( $# == 0 )); then
  set -- up -d --build
fi
exec docker compose "${compose_args[@]}" "$@"
