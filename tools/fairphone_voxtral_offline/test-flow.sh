#!/usr/bin/env bash
set -euo pipefail

MODEL="${1:-}"
AUDIO="${2:-./samples/test.wav}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'TXT'
Usage: bash test-flow.sh <model.gguf> [audio.wav]

Runs the reproducible Fairphone Voxtral offline smoke-test flow:
  1. check environment
  2. validate model path
  3. validate audio file
  4. detect compatible runner
  5. run transcription wrapper

Default audio path:
  ./samples/test.wav

Typical usage:
  bash record-test-audio.sh
  bash test-flow.sh ~/models/voxtral/voxtral-mini-4b-realtime-q4_k.gguf ./samples/test.wav
TXT
}

if [[ "${MODEL:-}" == "--help" || "${MODEL:-}" == "-h" || -z "$MODEL" ]]; then
  usage
  exit 2
fi

cd "$SCRIPT_DIR"

echo "[fairphone-voxtral] Step 1/5: environment"
bash ./check-env.sh

echo
echo "[fairphone-voxtral] Step 2/5: model"
if [[ ! -f "$MODEL" ]]; then
  echo "Model file not found: $MODEL" >&2
  exit 2
fi
echo "[ok] model: $MODEL"

echo
echo "[fairphone-voxtral] Step 3/5: audio"
bash ./validate-audio.sh "$AUDIO"

echo
echo "[fairphone-voxtral] Step 4/5: runner"
RESOLVED_RUNNER="$(bash ./detect-runner.sh)"
echo "[ok] runner: $RESOLVED_RUNNER"

echo
echo "[fairphone-voxtral] Step 5/5: transcription"
VOXTRAL_RUNNER="$RESOLVED_RUNNER" bash ./transcribe-test.sh "$MODEL" "$AUDIO"
