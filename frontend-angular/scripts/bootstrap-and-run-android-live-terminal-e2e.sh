#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend-angular"

ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-/tmp/android-sdk}}"
export ANDROID_SDK_ROOT
export ANDROID_HOME="$ANDROID_SDK_ROOT"
export PATH="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/emulator:$PATH"

ANDROID_API_LEVEL="${ANANTA_ANDROID_API_LEVEL:-35}"
ANDROID_ABI="${ANANTA_ANDROID_ABI:-x86_64}"
ANDROID_IMAGE_VENDOR="${ANANTA_ANDROID_IMAGE_VENDOR:-google_apis}"
ANDROID_IMAGE="system-images;android-${ANDROID_API_LEVEL};${ANDROID_IMAGE_VENDOR};${ANDROID_ABI}"
AVD_NAME="${ANANTA_ANDROID_AVD_NAME:-ananta-api${ANDROID_API_LEVEL}}"
AUTO_SHUTDOWN_STACK="${ANANTA_ANDROID_AUTO_SHUTDOWN_STACK:-1}"

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Fehlt: $1"
    exit 1
  fi
}

ensure_host_dependencies() {
  local required=(curl node npm python3 rg)
  for cmd in "${required[@]}"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "Fehlt: $cmd"
      exit 1
    fi
  done

  if command -v java >/dev/null 2>&1; then
    return
  fi

  if [[ "${ANANTA_AUTO_JAVA_INSTALL:-1}" != "1" ]]; then
    echo "Fehlt: java (setze ANANTA_AUTO_JAVA_INSTALL=1 fuer auto-download)."
    exit 1
  fi

  local toolchain_root="${ANANTA_TOOLCHAIN_ROOT:-/tmp/ananta-toolchain}"
  local jdk_root="${toolchain_root}/jdk-17"
  local jdk_tar="/tmp/ananta-jdk17.tar.gz"
  mkdir -p "$toolchain_root"
  if [[ ! -x "${jdk_root}/bin/java" ]]; then
    curl -fsSL "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse" -o "$jdk_tar"
    rm -rf "$jdk_root"
    mkdir -p "$jdk_root"
    tar -xzf "$jdk_tar" -C "$jdk_root" --strip-components=1
  fi
  export JAVA_HOME="$jdk_root"
  export PATH="$JAVA_HOME/bin:$PATH"
}

install_cmdline_tools_if_missing() {
  if command -v sdkmanager >/dev/null 2>&1; then
    return
  fi

  need_cmd curl
  need_cmd python3
  mkdir -p "$ANDROID_SDK_ROOT/cmdline-tools"
  local tmp_zip="/tmp/android-commandlinetools.zip"
  local unpack_dir="/tmp/android-commandlinetools"
  rm -rf "$unpack_dir"
  mkdir -p "$unpack_dir"

  local urls=(
    "https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip"
    "https://dl.google.com/android/repository/commandlinetools-linux-12996373_latest.zip"
    "https://dl.google.com/android/repository/commandlinetools-linux-12266719_latest.zip"
    "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
  )

  local downloaded=0
  for url in "${urls[@]}"; do
    if curl -fsSL "$url" -o "$tmp_zip"; then
      downloaded=1
      break
    fi
  done

  if [[ "$downloaded" -ne 1 ]]; then
    echo "Konnte Android commandline-tools nicht herunterladen."
    exit 1
  fi

  python3 - <<PY
import zipfile
zipfile.ZipFile("$tmp_zip").extractall("$unpack_dir")
PY
  rm -rf "$ANDROID_SDK_ROOT/cmdline-tools/latest"
  mv "$unpack_dir/cmdline-tools" "$ANDROID_SDK_ROOT/cmdline-tools/latest"
}

ensure_android_sdk() {
  install_cmdline_tools_if_missing
  yes | sdkmanager --sdk_root="$ANDROID_SDK_ROOT" --licenses >/dev/null
  sdkmanager --sdk_root="$ANDROID_SDK_ROOT" \
    "platform-tools" \
    "emulator" \
    "platforms;android-${ANDROID_API_LEVEL}" \
    "$ANDROID_IMAGE"
}

ensure_avd() {
  if avdmanager list avd | rg -q "Name: ${AVD_NAME}$"; then
    return
  fi
  echo "no" | avdmanager create avd \
    --name "$AVD_NAME" \
    --package "$ANDROID_IMAGE" \
    --abi "$ANDROID_IMAGE_VENDOR/$ANDROID_ABI"
}

start_stack() {
  "$ROOT_DIR/scripts/compose-test-stack.sh" up-live
}

stop_stack() {
  "$ROOT_DIR/scripts/compose-test-stack.sh" down || true
}

main() {
  need_cmd bash
  ensure_host_dependencies

  ensure_android_sdk
  ensure_avd

  trap 'if [[ "$AUTO_SHUTDOWN_STACK" == "1" ]]; then stop_stack; fi' EXIT

  start_stack
  cd "$FRONTEND_DIR"
  npm install
  ANANTA_ANDROID_AVD_NAME="$AVD_NAME" npm run test:e2e:android:terminal
}

main "$@"
