#!/usr/bin/env bash
set -euo pipefail

MODEL="${1:-}"
AUDIO="${2:-}"
LLAMA_DIR="${LLAMA_DIR:-$HOME/src/llama.cpp}"
RUNNER="${VOXTRAL_RUNNER:-}"

if [[ -z "$MODEL" || -z "$AUDIO" ]]; then
  echo "Usage: bash transcribe-test.sh <model.gguf> <audio.wav>" >&2
  echo "Example: bash transcribe-test.sh ~/models/voxtral/voxtral-mini-q4.gguf ./samples/test.wav" >&2
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
    "$LLAMA_DIR/build/bin/voxtral-stream-cli"
    "$LLAMA_DIR/build/bin/voxtral-cli"
    "$LLAMA_DIR/build/bin/llama-voxtral-cli"
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

This is expected until llama.cpp or another local runtime exposes a Voxtral audio CLI for Android/Termux.
Normal llama-cli text inference is not enough for audio transcription.

Set VOXTRAL_RUNNER manually if you have a compatible binary, for example:

  VOXTRAL_RUNNER=$HOME/src/llama.cpp/build/bin/voxtral-stream-cli \
    bash transcribe-test.sh ~/models/voxtral/model.gguf ./samples/test.wav

This wrapper does not fake transcription.
TXT
  exit 3
fi

echo "[fairphone-voxtral] Runner: $RESOLVED_RUNNER"
echo "[fairphone-voxtral] Model:  $MODEL"
echo "[fairphone-voxtral] Audio:  $AUDIO"

set -x
"$RESOLVED_RUNNER" -m "$MODEL" -f "$AUDIO"
