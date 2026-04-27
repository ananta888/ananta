#!/usr/bin/env bash
set -euo pipefail

MODEL="${1:-}"
AUDIO="${2:-}"
LLAMA_DIR="${LLAMA_DIR:-$HOME/src/llama.cpp}"
CRISPASR_DIR="${CRISPASR_DIR:-$HOME/src/CrispASR}"
RUNNER="${VOXTRAL_RUNNER:-}"

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

find_runner() {
  if [[ -n "$RUNNER" ]]; then
    echo "$RUNNER"
    return 0
  fi
  local candidates=(
    "$CRISPASR_DIR/build/bin/voxtral4b-main"
    "$CRISPASR_DIR/build/voxtral4b-main"
    "$LLAMA_DIR/build/bin/voxtral-stream-cli"
    "$LLAMA_DIR/build/bin/voxtral-cli"
    "$LLAMA_DIR/build/bin/llama-voxtral-cli"
    "$(command -v voxtral4b-main 2>/dev/null || true)"
    "$(command -v voxtral-stream-cli 2>/dev/null || true)"
    "$(command -v voxtral-cli 2>/dev/null || true)"
  )
  for candidate in "${candidates[@]}"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if ! RESOLVED_RUNNER="$(find_runner)"; then
  cat >&2 <<'TXT'
No Voxtral-compatible audio runner found.

Recommended first runner path for Fairphone/Termux:

  bash build-crispasr.sh

Then run:

  bash download-voxtral-model.sh q4_k
  bash transcribe-test.sh ~/models/voxtral/voxtral-mini-4b-realtime-q4_k.gguf ./samples/test.wav

Normal llama-cli text inference is not enough for audio transcription.
This wrapper does not fake transcription.
TXT
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
