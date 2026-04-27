#!/usr/bin/env bash
set -euo pipefail

MODEL="${1:-}"
AUDIO="${2:-}"

source "$(dirname "${BASH_SOURCE[0]}")/lib-runner-detect.sh"

if [[ -z "$MODEL" || -z "$AUDIO" ]]; then
  echo "Usage: bash transcribe-test.sh <model.gguf> <audio.wav>" >&2
  echo "Example: bash transcribe-test.sh ~/models/voxtral/voxtral-mini-4b-realtime-q4_k.gguf ./samples/test.wav" >&2
  exit 2
fi

if [[ ! -f "$MODEL" ]]; then
  echo "Model file not found: $MODEL" >&2
  exit 2
fi

if [[ ! -f "$AUDIO" ]]; then
  echo "Audio file not found: $AUDIO" >&2
  exit 2
fi

if ! RESOLVED_RUNNER=$(find_voxtral_runner); then
  cat >&2 <<'TXT'

Recommended first runner path for Fairphone/Termux:

  bash build-crispasr.sh

Then run:

  bash download-voxtral-model.sh q4_k
  bash transcribe-test.sh ~/models/voxtral/voxtral-mini-4b-realtime-q4_k.gguf ./samples/test.wav
TXT
  print_runner_not_found
  exit 3
fi

echo "[fairphone-voxtral] Runner: $RESOLVED_RUNNER"
echo "[fairphone-voxtral] Model:  $MODEL"
echo "[fairphone-voxtral] Audio:  $AUDIO"

case "$(basename "$RESOLVED_RUNNER")" in
  voxtral4b-main)
    set -x
    "$RESOLVED_RUNNER" "$MODEL" "$AUDIO"
    ;;
  *)
    set -x
    "$RESOLVED_RUNNER" -m "$MODEL" -f "$AUDIO"
    ;;
esac
