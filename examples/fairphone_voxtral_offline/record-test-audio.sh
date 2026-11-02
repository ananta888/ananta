#!/usr/bin/env bash
set -euo pipefail

SAMPLE_DIR="${SAMPLE_DIR:-$(pwd)/samples}"
SECONDS_TO_RECORD="${SECONDS_TO_RECORD:-5}"
OUT_FILE="${1:-$SAMPLE_DIR/test.wav}"
RAW_FILE="${OUT_FILE%.*}.m4a"
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

if ! command -v ffmpeg >/dev/null 2>&1; then
  cat >&2 <<'TXT'
[fairphone-voxtral] ffmpeg not found.

Install it first:

  pkg install ffmpeg
TXT
  exit 2
fi

echo "[fairphone-voxtral] Recording ${SECONDS_TO_RECORD}s to raw file: $RAW_FILE"
echo "[fairphone-voxtral] Final WAV will be: $OUT_FILE"
echo "Speak now..."
rm -f "$OUT_FILE" "$RAW_FILE" "$LOG_FILE"

if ! termux-microphone-record -f "$RAW_FILE" -l "$SECONDS_TO_RECORD" >"$LOG_FILE" 2>&1; then
  cat "$LOG_FILE" >&2 || true
  if grep -qiE "RECORD_AUDIO|permission|grant" "$LOG_FILE"; then
    print_permission_help
  fi
  exit 1
fi

# Termux:API may return immediately after starting the Android MediaRecorder.
# Wait for the requested duration plus a small finalization buffer, then stop any active recorder.
sleep "$((SECONDS_TO_RECORD + 2))"

# Stop the recorder with timeout protection
if ! timeout 5 termux-microphone-record -q >>"$LOG_FILE" 2>&1; then
  echo "[warn] termux-microphone-record -q did not respond within 5 seconds (this may be normal)" >>"$LOG_FILE"
fi
sleep 1

if [[ ! -s "$RAW_FILE" ]]; then
  cat "$LOG_FILE" >&2 || true
  if grep -qiE "RECORD_AUDIO|permission|grant" "$LOG_FILE"; then
    print_permission_help
  else
    cat >&2 <<TXT
[fairphone-voxtral] Recording command finished, but no audio file was created:
$RAW_FILE

Check:
- Termux microphone permission
- Termux:API app is installed
- No other app is blocking microphone access
TXT
  fi
  exit 1
fi

if ! ffprobe -hide_banner "$RAW_FILE" >"${RAW_FILE}.ffprobe.log" 2>&1; then
  cat "${RAW_FILE}.ffprobe.log" >&2 || true
  cat >&2 <<TXT
[fairphone-voxtral] Android created an audio file, but ffprobe could not read it:
$RAW_FILE

Try again. If it keeps failing, increase the duration:

  SECONDS_TO_RECORD=10 bash record-test-audio.sh
TXT
  exit 1
fi

echo "[fairphone-voxtral] Converting to 16 kHz mono WAV..."
ffmpeg -y -hide_banner -loglevel warning -i "$RAW_FILE" -ac 1 -ar 16000 "$OUT_FILE"

if [[ ! -s "$OUT_FILE" ]]; then
  echo "[fairphone-voxtral] WAV conversion failed or produced an empty file: $OUT_FILE" >&2
  exit 1
fi

echo "[fairphone-voxtral] Recorded raw file: $RAW_FILE"
echo "[fairphone-voxtral] Recorded WAV file: $OUT_FILE"
ffprobe -hide_banner "$OUT_FILE" || true
