#!/usr/bin/env bash
set -euo pipefail

SAMPLE_DIR="${SAMPLE_DIR:-$(pwd)/samples}"
SECONDS_TO_RECORD="${SECONDS_TO_RECORD:-5}"
OUT_FILE="${1:-$SAMPLE_DIR/test.wav}"

mkdir -p "$(dirname "$OUT_FILE")"

if ! command -v termux-microphone-record >/dev/null 2>&1; then
  echo "termux-microphone-record not found." >&2
  echo "Install Termux:API app from F-Droid and run: pkg install termux-api" >&2
  exit 2
fi

echo "[fairphone-voxtral] Recording ${SECONDS_TO_RECORD}s to $OUT_FILE"
echo "Speak now..."
termux-microphone-record -f "$OUT_FILE" -l "$SECONDS_TO_RECORD"

echo "[fairphone-voxtral] Recorded file: $OUT_FILE"
if command -v ffprobe >/dev/null 2>&1; then
  ffprobe -hide_banner "$OUT_FILE" || true
fi
