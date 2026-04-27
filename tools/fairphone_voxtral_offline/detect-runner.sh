#!/usr/bin/env bash
set -euo pipefail

PRINT_EXPORT="${PRINT_EXPORT:-0}"

source "$(dirname "${BASH_SOURCE[0]}")/lib-runner-detect.sh"

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

if runner=$(find_voxtral_runner); then
  if [[ "$PRINT_EXPORT" == "1" ]]; then
    printf 'VOXTRAL_RUNNER=%q\n' "$runner"
  else
    echo "$runner"
  fi
  exit 0
fi

print_runner_not_found
exit 3
