#!/usr/bin/env bash
set -euo pipefail

LLAMA_DIR="${LLAMA_DIR:-$HOME/src/llama.cpp}"
CRISPASR_DIR="${CRISPASR_DIR:-$HOME/src/CrispASR}"
RUNNER="${VOXTRAL_RUNNER:-}"
PRINT_EXPORT="${PRINT_EXPORT:-0}"

usage() {
  cat <<'TXT'
Usage: bash detect-runner.sh [--export]

Detects a local Voxtral-compatible audio runner without executing transcription.

Environment overrides:
  VOXTRAL_RUNNER=/path/to/runner
  LLAMA_DIR=$HOME/src/llama.cpp
  CRISPASR_DIR=$HOME/src/CrispASR

Exit codes:
  0 runner found
  3 no compatible runner found
TXT
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--export" ]]; then
  PRINT_EXPORT=1
fi

candidates=()

if [[ -n "$RUNNER" ]]; then
  candidates+=("$RUNNER")
fi

candidates+=(
  "$CRISPASR_DIR/build/bin/voxtral4b-main"
  "$CRISPASR_DIR/build/voxtral4b-main"
  "$LLAMA_DIR/build/bin/voxtral-stream-cli"
  "$LLAMA_DIR/build/bin/voxtral-cli"
  "$LLAMA_DIR/build/bin/llama-voxtral-cli"
)

for name in voxtral4b-main voxtral-stream-cli voxtral-cli llama-voxtral-cli; do
  resolved="$(command -v "$name" 2>/dev/null || true)"
  if [[ -n "$resolved" ]]; then
    candidates+=("$resolved")
  fi
done

for candidate in "${candidates[@]}"; do
  if [[ -n "$candidate" && -x "$candidate" ]]; then
    if [[ "$PRINT_EXPORT" == "1" ]]; then
      printf 'VOXTRAL_RUNNER=%q\n' "$candidate"
    else
      echo "$candidate"
    fi
    exit 0
  fi
done

cat >&2 <<TXT
No Voxtral-compatible audio runner found.

Checked:
  VOXTRAL_RUNNER=${RUNNER:-<unset>}
  $CRISPASR_DIR/build/bin/voxtral4b-main
  $CRISPASR_DIR/build/voxtral4b-main
  $LLAMA_DIR/build/bin/voxtral-stream-cli
  $LLAMA_DIR/build/bin/voxtral-cli
  $LLAMA_DIR/build/bin/llama-voxtral-cli
  PATH: voxtral4b-main, voxtral-stream-cli, voxtral-cli, llama-voxtral-cli

Normal llama-cli text inference is not enough for Voxtral audio transcription.
TXT
exit 3
