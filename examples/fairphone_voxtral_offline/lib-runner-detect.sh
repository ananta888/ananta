#!/usr/bin/env bash
# Shared runner detection library for fairphone-voxtral scripts
# Source this file: source ./lib-runner-detect.sh

# find_voxtral_runner finds a compatible Voxtral audio runner
# Outputs: Path to executable runner, or nothing if not found
# Returns: 0 if runner found, non-zero otherwise
find_voxtral_runner() {
  local llama_dir="${LLAMA_DIR:-$HOME/src/llama.cpp}"
  local crispasr_dir="${CRISPASR_DIR:-$HOME/src/CrispASR}"
  local runner="${VOXTRAL_RUNNER:-}"
  
  local candidates=()
  
  # Environment override has highest priority
  if [[ -n "$runner" ]]; then
    candidates+=("$runner")
  fi
  
  # Explicit build paths
  candidates+=(
    "$crispasr_dir/build/bin/voxtral4b-main"
    "$crispasr_dir/build/voxtral4b-main"
    "$llama_dir/build/bin/voxtral-stream-cli"
    "$llama_dir/build/bin/voxtral-cli"
    "$llama_dir/build/bin/llama-voxtral-cli"
  )
  
  # PATH lookup
  for name in voxtral4b-main voxtral-stream-cli voxtral-cli llama-voxtral-cli; do
    local resolved
    resolved="$(command -v "$name" 2>/dev/null || true)"
    if [[ -n "$resolved" ]]; then
      candidates+=("$resolved")
    fi
  done
  
  for candidate in "${candidates[@]}"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  
  return 1
}

# print_runner_not_found prints error message when no runner is found
# Arguments: optional custom message suffix
print_runner_not_found() {
  local llama_dir="${LLAMA_DIR:-$HOME/src/llama.cpp}"
  local crispasr_dir="${CRISPASR_DIR:-$HOME/src/CrispASR}"
  local runner="${VOXTRAL_RUNNER:-}"
  local suffix="${1:-}"
  
  cat >&2 <<TXT
No Voxtral-compatible audio runner found.

Checked:
  VOXTRAL_RUNNER=${runner:-<unset>}
  $crispasr_dir/build/bin/voxtral4b-main
  $crispasr_dir/build/voxtral4b-main
  $llama_dir/build/bin/voxtral-stream-cli
  $llama_dir/build/bin/voxtral-cli
  $llama_dir/build/bin/llama-voxtral-cli
  PATH: voxtral4b-main, voxtral-stream-cli, voxtral-cli, llama-voxtral-cli

Normal llama-cli text inference is not enough for Voxtral audio transcription.

$suffix
TXT
}
