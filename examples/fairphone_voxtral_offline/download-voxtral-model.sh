#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-$HOME/models/voxtral}"
VARIANT="${1:-q4_k}"
mkdir -p "$MODEL_DIR"

case "$VARIANT" in
  q4_k)
    FILE="voxtral-mini-4b-realtime-q4_k.gguf"
    ;;
  q5_k)
    FILE="voxtral-mini-4b-realtime-q5_k.gguf"
    ;;
  q8_0)
    FILE="voxtral-mini-4b-realtime-q8_0.gguf"
    ;;
  *)
    cat >&2 <<TXT
Unknown variant: $VARIANT

Supported variants:
  q4_k   recommended first Fairphone test
  q5_k   larger/slower, better quality
  q8_0   much larger, probably too heavy for Fairphone 6
TXT
    exit 2
    ;;
esac

URL="https://huggingface.co/cstr/voxtral-mini-4b-realtime-GGUF/resolve/main/${FILE}"
TARGET="$MODEL_DIR/$FILE"

echo "[fairphone-voxtral] Downloading $FILE"
echo "[fairphone-voxtral] Target: $TARGET"

if command -v curl >/dev/null 2>&1; then
  curl -L --fail --continue-at - -o "$TARGET" "$URL"
elif command -v wget >/dev/null 2>&1; then
  wget -c -O "$TARGET" "$URL"
else
  echo "Need curl or wget." >&2
  exit 2
fi

if [[ ! -s "$TARGET" ]]; then
  echo "Download failed or empty model file: $TARGET" >&2
  exit 1
fi

cat <<TXT

[fairphone-voxtral] Model ready:
$TARGET

Run:
  bash transcribe-test.sh "$TARGET" ./samples/test.wav

If the runner is not auto-detected:
  VOXTRAL_RUNNER=~/src/CrispASR/build/bin/voxtral4b-main bash transcribe-test.sh "$TARGET" ./samples/test.wav
TXT
