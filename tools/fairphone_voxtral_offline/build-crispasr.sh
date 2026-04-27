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

echo "[fairphone-voxtral] Available build targets containing voxtral/transcribe/asr/main:"
cmake --build build --target help 2>/dev/null | grep -Ei 'voxtral|transcribe|asr|main' || true

echo "[fairphone-voxtral] Building CrispASR default target with -j${JOBS}..."
cmake --build build -j"$JOBS"

echo "[fairphone-voxtral] Searching for executable runner candidates..."
mapfile -t RUNNERS < <(
  find "$CRISPASR_DIR/build" -type f -perm -111 2>/dev/null \
    | grep -Ei '/(voxtral|transcribe|asr|main)[^/]*$' \
    | sort
)

if [[ "${#RUNNERS[@]}" -eq 0 ]]; then
  cat >&2 <<TXT
[fairphone-voxtral] Build finished, but no likely Voxtral/ASR runner executable was found.

Please inspect the generated targets on your Fairphone:

  cd "$CRISPASR_DIR"
  cmake --build build --target help | grep -Ei 'voxtral|transcribe|asr|main'
  find build -type f -perm -111 | sort | head -100

If you find the right binary, run transcription manually with:

  VOXTRAL_RUNNER=/path/to/binary bash transcribe-test.sh ~/models/voxtral/model.gguf ./samples/test.wav
TXT
  exit 1
fi

printf '%s\n' "${RUNNERS[@]}" > "$CRISPASR_DIR/build/fairphone-voxtral-runner-candidates.txt"
BEST_RUNNER="${RUNNERS[0]}"
for runner in "${RUNNERS[@]}"; do
  case "$(basename "$runner")" in
    *voxtral*)
      BEST_RUNNER="$runner"
      break
      ;;
  esac
done

cat <<TXT

[fairphone-voxtral] Runner candidates:
$(printf '  - %s\n' "${RUNNERS[@]}")

[fairphone-voxtral] Suggested runner:
$BEST_RUNNER

Next:
  bash download-voxtral-model.sh q4_k
  VOXTRAL_RUNNER="$BEST_RUNNER" bash transcribe-test.sh ~/models/voxtral/voxtral-mini-4b-realtime-q4_k.gguf ./samples/test.wav
TXT
