#!/usr/bin/env bash
# Prepare or run versioned local SFT-LoRA candidates. No candidate is promoted here.
set -euo pipefail

TARGET="${1:-}"
DATASET="${2:-}"
RUNTIME_ROOT="${ANANTA_LOCAL_MODEL_ROOT:-/home/krusty/moe-test}"
OUTPUT_ROOT="${ANANTA_LOCAL_ADAPTER_OUTPUT_ROOT:-/home/krusty/ananta/project-workspaces/local-adapter-training/candidates}"
CPU_SET="${ANANTA_ADAPTER_TRAINING_CPU_SET:-16-19}"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[ "$TARGET" = needle2 ] || [ "$TARGET" = lfm2.5-2.6b-agentic ] || \
    fail "usage: $0 {needle2|lfm2.5-2.6b-agentic} DATASET.jsonl"
[ -f "$DATASET" ] || fail "dataset does not exist: $DATASET"

DATASET_SHA="$(sha256sum "$DATASET" | awk '{print $1}')"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
CANDIDATE_ID="${TARGET//./-}-$STAMP-${DATASET_SHA:0:12}"
CANDIDATE_DIR="$OUTPUT_ROOT/$CANDIDATE_ID"
mkdir -p "$CANDIDATE_DIR"

case "$TARGET" in
    needle2)
        NEEDLE_BIN="${ANANTA_NEEDLE_BIN:-$RUNTIME_ROOT/.venv-needle/bin/needle}"
        BASE_CHECKPOINT="${ANANTA_NEEDLE_BASE_CHECKPOINT:-}"
        [ -x "$NEEDLE_BIN" ] || fail "needle executable missing: $NEEDLE_BIN"
        [ -f "$BASE_CHECKPOINT" ] || fail \
            "Needle training requires the unquantized base checkpoint; a .cact inference blob is insufficient"
        # Explicitly disable upstream data generation: collected output is not
        # allowed to become its own ground truth.
        env JAX_PLATFORMS=cpu XLA_PYTHON_CLIENT_PREALLOCATE=false \
            nice -n 15 taskset --cpu-list "$CPU_SET" "$NEEDLE_BIN" finetune "$DATASET" \
            --checkpoint "$BASE_CHECKPOINT" --epochs "${ANANTA_NEEDLE_EPOCHS:-3}" \
            --batch-size "${ANANTA_NEEDLE_BATCH_SIZE:-8}" --lr "${ANANTA_NEEDLE_LR:-0.0001}" \
            --lora-rank "${ANANTA_NEEDLE_LORA_RANK:-8}" \
            --lora-alpha "${ANANTA_NEEDLE_LORA_ALPHA:-16}" \
            --max-len 256 --val-split 0.2 --generate 0 \
            --checkpoint-dir "$CANDIDATE_DIR/checkpoints" \
            --out "$CANDIDATE_DIR/adapter.pkl"
        ;;
    lfm2.5-2.6b-agentic)
        BASE_SNAPSHOT="${ANANTA_LFM_SFT_BASE_SNAPSHOT:-}"
        [ -f "$BASE_SNAPSHOT/config.json" ] || fail \
            "LFM SFT requires a pinned Transformers snapshot; the installed Q8_0 GGUF is inference-only"
        [ -n "${ANANTA_LFM_SFT_BASE_MODEL_ID:-}" ] || fail "ANANTA_LFM_SFT_BASE_MODEL_ID is required"
        [ -n "${ANANTA_LFM_SFT_BASE_SNAPSHOT_HASH:-}" ] || fail "ANANTA_LFM_SFT_BASE_SNAPSHOT_HASH is required"
        ACTUAL_BASE_HASH="$(python3 -c 'from pathlib import Path; import sys; from scripts.run_lora_training_smoke import _tree_sha256; print(_tree_sha256(Path(sys.argv[1])))' "$BASE_SNAPSHOT")"
        [ "$ACTUAL_BASE_HASH" = "$ANANTA_LFM_SFT_BASE_SNAPSHOT_HASH" ] || fail "LFM base snapshot hash mismatch"
        [ -n "${ANANTA_TRAINING_SOURCE_IDS:-}" ] || fail "verified SRC_* IDs are required; none are invented"
        [ -n "${ANANTA_TRAINING_RUN_IDS:-}" ] || fail "verified RUN_* IDs are required; none are invented"
        # The Hub API remains task owner. This file is an immutable request
        # draft and cannot call or promote a Worker by itself.
        env CANDIDATE_ID="$CANDIDATE_ID" python3 - "$CANDIDATE_DIR/request.json" <<'PY'
import json, os, sys
payload = {
  "dataset_id": os.environ.get("ANANTA_LFM_DATASET_ID", ""),
  "job_type": "train_lora", "mode": "live", "backend": "peft_trl",
  "base_model": os.environ["ANANTA_LFM_SFT_BASE_MODEL_ID"], "method": "lora",
  "gpu_profile": "none", "output_name": os.environ["CANDIDATE_ID"], "live_confirmed": True,
  "source_ids": os.environ["ANANTA_TRAINING_SOURCE_IDS"].split(","),
  "run_ids": os.environ["ANANTA_TRAINING_RUN_IDS"].split(","),
  "hyperparameters": {"seed": 42, "max_steps": 100, "evaluation_steps": 10,
    "learning_rate": 0.0001, "batch_size": 1, "gradient_accumulation_steps": 8,
    "lora_rank": 8, "lora_alpha": 16, "lora_dropout": 0.05,
    "max_seq_length": 1024, "load_in_4bit": False, "early_stopping_patience": 3}
}
if not payload["dataset_id"]:
    raise SystemExit("ANANTA_LFM_DATASET_ID is required")
with open(sys.argv[1], "x", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
        printf 'Hub-owned request prepared: %s\n' "$CANDIDATE_DIR/request.json"
        ;;
esac

printf '%s  %s\n' "$DATASET_SHA" "$DATASET" > "$CANDIDATE_DIR/dataset.sha256"
printf 'Candidate %s created; offline, shadow and canary gates remain mandatory.\n' "$CANDIDATE_ID"
