#!/usr/bin/env bash
set -euo pipefail

LLAMA_DIR="${LLAMA_DIR:-$HOME/src/llama.cpp}"
JOBS="${JOBS:-4}"

mkdir -p "$(dirname "$LLAMA_DIR")"

if [[ ! -d "$LLAMA_DIR/.git" ]]; then
  echo "[fairphone-voxtral] Cloning llama.cpp into $LLAMA_DIR"
  git clone https://github.com/ggml-org/llama.cpp "$LLAMA_DIR"
else
  echo "[fairphone-voxtral] Updating existing llama.cpp checkout in $LLAMA_DIR"
  git -C "$LLAMA_DIR" pull --ff-only
fi

cd "$LLAMA_DIR"

echo "[fairphone-voxtral] Configuring llama.cpp..."
cmake -B build -DLLAMA_NATIVE=OFF -DCMAKE_BUILD_TYPE=Release

echo "[fairphone-voxtral] Building llama.cpp with -j${JOBS}..."
cmake --build build -j"$JOBS"

cat <<TXT

[fairphone-voxtral] Build finished.

llama.cpp path:
$LLAMA_DIR

Binaries:
$LLAMA_DIR/build/bin

Note:
Voxtral audio transcription may require a Voxtral-compatible audio runner, not only normal llama-cli text inference.
TXT
