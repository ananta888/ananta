#!/usr/bin/env bash
set -euo pipefail

CRISPASR_DIR="${CRISPASR_DIR:-$HOME/src/CrispASR}"
JOBS="${JOBS:-4}"

mkdir -p "$(dirname "$CRISPASR_DIR")"

if [[ ! -d "$CRISPASR_DIR/.git" ]]; then
  echo "[fairphone-voxtral] Cloning CrispASR into $CRISPASR_DIR"
  git clone https://github.com/CrispStrobe/CrispASR "$CRISPASR_DIR"
else
  echo "[fairphone-voxtral] Updating existing CrispASR checkout in $CRISPASR_DIR"
  git -C "$CRISPASR_DIR" pull --ff-only
fi

cd "$CRISPASR_DIR"

echo "[fairphone-voxtral] Configuring CrispASR..."
cmake -B build -DCMAKE_BUILD_TYPE=Release

echo "[fairphone-voxtral] Building voxtral4b-main with -j${JOBS}..."
cmake --build build -j"$JOBS" --target voxtral4b-main

if [[ ! -x "$CRISPASR_DIR/build/bin/voxtral4b-main" ]]; then
  echo "[fairphone-voxtral] Build finished, but runner was not found:" >&2
  echo "$CRISPASR_DIR/build/bin/voxtral4b-main" >&2
  exit 1
fi

cat <<TXT

[fairphone-voxtral] CrispASR Voxtral runner built:
$CRISPASR_DIR/build/bin/voxtral4b-main

Next:
  bash download-voxtral-model.sh q4_k
  VOXTRAL_RUNNER=$CRISPASR_DIR/build/bin/voxtral4b-main bash transcribe-test.sh ~/models/voxtral/voxtral-mini-4b-realtime-q4_k.gguf ./samples/test.wav
TXT
