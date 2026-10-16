#!/usr/bin/env bash
# Whisper.cpp Fork Setup & Upstream Pinning
set -euo pipefail

UPSTREAM_REPO="https://github.com/ggerganov/whisper.cpp.git"
PIN_TAG="v1.5.0" # Beispiel-Pin, anpassen nach Bedarf
FORK_DIR="./whisper_fork"

# Fork klonen und auf Pin-Tag auschecken
git clone "$UPSTREAM_REPO" "$FORK_DIR"
cd "$FORK_DIR"
git checkout "$PIN_TAG"

echo "Whisper.cpp Fork auf Tag $PIN_TAG ausgecheckt."
