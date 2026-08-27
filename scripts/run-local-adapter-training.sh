#!/usr/bin/env bash
# Prepare a closed Hub training request. This script never starts training.
set -euo pipefail

TARGET="${1:-}"
DATASET_ID="${2:-}"
OUTPUT_ROOT="${ANANTA_LOCAL_ADAPTER_OUTPUT_ROOT:-/home/krusty/ananta/project-workspaces/local-adapter-training/requests}"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[ "$TARGET" = needle2 ] || [ "$TARGET" = lfm2.5-2.6b-agentic ] || \
    fail "usage: $0 {needle2|lfm2.5-2.6b-agentic} DATASET_ID"
[[ "$DATASET_ID" =~ ^ds-[a-f0-9]{32}$ ]] || fail "a catalog-owned dataset ID is required"
[ -n "${ANANTA_TRAINING_SOURCE_IDS:-}" ] || fail "verified SRC_* IDs are required; none are invented"
[ -n "${ANANTA_TRAINING_RUN_IDS:-}" ] || fail "verified RUN_* IDs are required; none are invented"

case "$TARGET" in
    needle2)
        BACKEND="needle"
        BASE_MODEL_ID="${ANANTA_NEEDLE_BASE_MODEL_ID:-}"
        [ -n "$BASE_MODEL_ID" ] || fail "ANANTA_NEEDLE_BASE_MODEL_ID is required"
        GPU_PROFILE="none"
        MAX_SEQUENCE_LENGTH=256
        MAX_STEPS="${ANANTA_NEEDLE_MAX_STEPS:-100}"
        BATCH_SIZE="${ANANTA_NEEDLE_BATCH_SIZE:-2}"
        ;;
    lfm2.5-2.6b-agentic)
        BACKEND="peft_trl"
        BASE_MODEL_ID="${ANANTA_LFM_SFT_BASE_MODEL_ID:-}"
        [ -n "$BASE_MODEL_ID" ] || fail "ANANTA_LFM_SFT_BASE_MODEL_ID is required"
        GPU_PROFILE="rtx3080-safe"
        MAX_SEQUENCE_LENGTH=1024
        MAX_STEPS="${ANANTA_LFM_MAX_STEPS:-100}"
        BATCH_SIZE=1
        ;;
esac

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REQUEST_ID="${TARGET//./-}-$STAMP-${DATASET_ID:3:12}"
REQUEST_DIR="$OUTPUT_ROOT/$REQUEST_ID"
mkdir -p "$REQUEST_DIR"

env \
    BACKEND="$BACKEND" \
    BASE_MODEL_ID="$BASE_MODEL_ID" \
    BATCH_SIZE="$BATCH_SIZE" \
    DATASET_ID="$DATASET_ID" \
    GPU_PROFILE="$GPU_PROFILE" \
    MAX_SEQUENCE_LENGTH="$MAX_SEQUENCE_LENGTH" \
    MAX_STEPS="$MAX_STEPS" \
    REQUEST_ID="$REQUEST_ID" \
    TARGET="$TARGET" \
    python3 - "$REQUEST_DIR/request.json" <<'PY'
import json
import os
import sys

payload = {
    "dataset_id": os.environ["DATASET_ID"],
    "job_type": "train_lora",
    "mode": "live",
    "backend": os.environ["BACKEND"],
    "release_target": os.environ["TARGET"],
    "base_model": os.environ["BASE_MODEL_ID"],
    "method": "lora",
    "gpu_profile": os.environ["GPU_PROFILE"],
    "output_name": os.environ["REQUEST_ID"],
    "live_confirmed": True,
    "source_ids": os.environ["ANANTA_TRAINING_SOURCE_IDS"].split(","),
    "run_ids": os.environ["ANANTA_TRAINING_RUN_IDS"].split(","),
    "hyperparameters": {
        "seed": 42,
        "max_steps": int(os.environ["MAX_STEPS"]),
        "evaluation_steps": 10,
        "learning_rate": 0.0001,
        "batch_size": int(os.environ["BATCH_SIZE"]),
        "gradient_accumulation_steps": 8,
        "lora_rank": 8,
        "lora_alpha": 16,
        "lora_dropout": 0.05,
        "max_sequence_length": int(os.environ["MAX_SEQUENCE_LENGTH"]),
        "load_in_4bit": False,
        "early_stopping_patience": 3,
    },
}
with open(sys.argv[1], "x", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY

printf 'Hub-owned request prepared: %s\n' "$REQUEST_DIR/request.json"
printf 'Submit it through the authenticated Hub ML-intern jobs API; no training was started.\n'
