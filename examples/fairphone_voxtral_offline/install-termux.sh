#!/usr/bin/env bash
set -euo pipefail

if ! command -v pkg >/dev/null 2>&1; then
  echo "This script is intended for Termux on Android." >&2
  echo "Install Termux from F-Droid and run it there." >&2
  exit 2
fi

echo "[fairphone-voxtral] Updating Termux package metadata..."
pkg update -y

echo "[fairphone-voxtral] Installing required packages..."
pkg install -y \
  git \
  cmake \
  clang \
  make \
  python \
  ffmpeg \
  wget \
  curl \
  termux-api

cat <<'TXT'

[fairphone-voxtral] Base packages installed.

Recommended Android app:
- Termux:API from F-Droid

Then allow microphone permission for Termux/Termux:API before recording.
TXT
