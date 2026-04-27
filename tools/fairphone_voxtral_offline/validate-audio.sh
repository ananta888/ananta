#!/usr/bin/env bash
set -euo pipefail

AUDIO="${1:-}"
MAX_SECONDS="${MAX_SECONDS:-30}"

usage() {
  cat <<'TXT'
Usage: bash validate-audio.sh <audio.wav>

Validates that a local test audio file exists and is usable for a small offline
Voxtral transcription smoke test.

Environment overrides:
  MAX_SECONDS=30
TXT
}

if [[ "${AUDIO:-}" == "--help" || "${AUDIO:-}" == "-h" || -z "$AUDIO" ]]; then
  usage
  exit 2
fi

if [[ ! -f "$AUDIO" ]]; then
  echo "Audio file not found: $AUDIO" >&2
  exit 2
fi

if [[ ! -s "$AUDIO" ]]; then
  echo "Audio file is empty: $AUDIO" >&2
  exit 2
fi

if ! command -v ffprobe >/dev/null 2>&1; then
  echo "[warn] ffprobe not found; falling back to basic file checks only."
  echo "[ok] audio exists and is non-empty: $AUDIO"
  exit 0
fi

DURATION="$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$AUDIO" || true)"
CODEC="$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of default=nk=1:nw=1 "$AUDIO" || true)"
SAMPLE_RATE="$(ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate -of default=nk=1:nw=1 "$AUDIO" || true)"
CHANNELS="$(ffprobe -v error -select_streams a:0 -show_entries stream=channels -of default=nk=1:nw=1 "$AUDIO" || true)"

if [[ -z "$DURATION" || -z "$CODEC" ]]; then
  echo "Audio metadata could not be read: $AUDIO" >&2
  exit 2
fi

python - "$DURATION" "$MAX_SECONDS" <<'PY'
import sys
try:
    duration = float(sys.argv[1])
    max_seconds = float(sys.argv[2])
except ValueError:
    print("Invalid duration metadata", file=sys.stderr)
    sys.exit(2)

if duration <= 0:
    print("Audio duration must be > 0 seconds", file=sys.stderr)
    sys.exit(2)
if duration > max_seconds:
    print(f"Audio duration {duration:.2f}s exceeds MAX_SECONDS={max_seconds:.2f}s", file=sys.stderr)
    sys.exit(2)
PY

echo "[ok] audio: $AUDIO"
echo "[ok] duration: ${DURATION}s"
echo "[ok] codec: $CODEC"
echo "[ok] sample_rate: ${SAMPLE_RATE:-unknown}"
echo "[ok] channels: ${CHANNELS:-unknown}"
