#!/usr/bin/env bash
set -euo pipefail

SAMPLE_DIR="${SAMPLE_DIR:-$(pwd)/samples}"
SECONDS_TO_RECORD="${SECONDS_TO_RECORD:-5}"
OUT_FILE="${1:-$SAMPLE_DIR/test.wav}"
LOG_FILE="${OUT_FILE}.record.log"

mkdir -p "$(dirname "$OUT_FILE")"

print_permission_help() {
  cat >&2 <<'TXT'

[fairphone-voxtral] Recording failed because Termux has no microphone access.

Do this on Android:

1. Open Settings
2. Apps
3. Termux
4. Permissions
5. Allow Microphone

If you installed Termux:API as a separate app, also check:

1. Settings
2. Apps
3. Termux:API
4. Permissions
5. Allow Microphone

Then run again:

  bash record-test-audio.sh

Optional storage setup, if you want to copy audio files from Android folders:

  termux-setup-storage

TXT
}

if ! command -v termux-microphone-record >/dev/null 2>&1; then
  cat >&2 <<'TXT'
[fairphone-voxtral] termux-microphone-record not found.

Install Termux:API support:

  pkg install termux-api

Also install the separate "Termux:API" Android app from F-Droid.
Then allow microphone permission for Termux and Termux:API in Android settings.
TXT
  exit 2
fi

echo "[fairphone-voxtral] Recording ${SECONDS_TO_RECORD}s to $OUT_FILE"
echo "Speak now..."
rm -f "$OUT_FILE" "$LOG_FILE"

if ! termux-microphone-record -f "$OUT_FILE" -l "$SECONDS_TO_RECORD" >"$LOG_FILE" 2>&1; then
  cat "$LOG_FILE" >&2 || true
  if grep -qiE "RECORD_AUDIO|permission|grant" "$LOG_FILE"; then
    print_permission_help
  fi
  exit 1
fi

if [[ ! -s "$OUT_FILE" ]]; then
  cat "$LOG_FILE" >&2 || true
  if grep -qiE "RECORD_AUDIO|permission|grant" "$LOG_FILE"; then
    print_permission_help
  else
    cat >&2 <<TXT
[fairphone-voxtral] Recording command finished, but no audio file was created:
$OUT_FILE

Check:
- Termux microphone permission
- Termux:API app is installed
- No other app is blocking microphone access
TXT
  fi
  exit 1
fi

echo "[fairphone-voxtral] Recorded file: $OUT_FILE"
if command -v ffprobe >/dev/null 2>&1; then
  ffprobe -hide_banner "$OUT_FILE" || true
fi
