#!/usr/bin/env bash
set -euo pipefail

LLAMA_DIR="${LLAMA_DIR:-$HOME/src/llama.cpp}"
MODEL_DIR="${MODEL_DIR:-$HOME/models/voxtral}"
SAMPLE_DIR="${SAMPLE_DIR:-$(pwd)/samples}"

check_cmd() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    echo "[ok] $name: $(command -v "$name")"
  else
    echo "[missing] $name"
  fi
}

echo "[fairphone-voxtral] Environment"
echo "HOME=$HOME"
echo "PWD=$(pwd)"
echo "LLAMA_DIR=$LLAMA_DIR"
echo "MODEL_DIR=$MODEL_DIR"
echo "SAMPLE_DIR=$SAMPLE_DIR"
echo

check_cmd git
check_cmd cmake
check_cmd clang
check_cmd make
check_cmd python
check_cmd ffmpeg
check_cmd termux-microphone-record
check_cmd termux-setup-storage

echo
if [[ -d "$LLAMA_DIR" ]]; then
  echo "[ok] llama.cpp directory exists"
else
  echo "[missing] llama.cpp directory: $LLAMA_DIR"
fi

if [[ -d "$LLAMA_DIR/build/bin" ]]; then
  echo "[ok] llama.cpp build/bin exists"
  find "$LLAMA_DIR/build/bin" -maxdepth 1 -type f -executable | sed 's#^#  - #' | head -30
else
  echo "[missing] llama.cpp build/bin"
fi

mkdir -p "$MODEL_DIR" "$SAMPLE_DIR"
echo
find "$MODEL_DIR" -maxdepth 1 -type f | sed 's#^#model: #' || true
find "$SAMPLE_DIR" -maxdepth 1 -type f | sed 's#^#sample: #' || true
