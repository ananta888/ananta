#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend-angular"
ANDROID_DIR="$FRONTEND_DIR/android"

AVD_NAME="${ANANTA_ANDROID_AVD_NAME:-ananta-api35}"
EMULATOR_SERIAL="${ANANTA_ANDROID_EMULATOR_SERIAL:-emulator-5554}"
USERNAME="${ANANTA_E2E_ADMIN_USER:-admin}"
PASSWORD="${ANANTA_E2E_ADMIN_PASSWORD:-AnantaAdminPassword123!}"
HOST_PORTS="${ANANTA_ANDROID_REVERSE_PORTS:-4200 5500 5501 5502 11434}"
EMULATOR_ARGS="${ANANTA_ANDROID_EMULATOR_ARGS:--no-snapshot-load -netdelay none -netspeed full}"
ANDROID_API_LEVEL="${ANANTA_ANDROID_API_LEVEL:-35}"
ANDROID_ABI="${ANANTA_ANDROID_ABI:-x86_64}"
ANDROID_IMAGE_VENDOR="${ANANTA_ANDROID_IMAGE_VENDOR:-google_apis}"
ANDROID_IMAGE="system-images;android-${ANDROID_API_LEVEL};${ANDROID_IMAGE_VENDOR};${ANDROID_ABI}"

if ! command -v adb >/dev/null 2>&1; then
  echo "adb nicht gefunden. Android SDK Platform-Tools installieren."
  exit 1
fi

if ! command -v emulator >/dev/null 2>&1; then
  echo "emulator binary nicht gefunden. Android SDK Emulator installieren."
  exit 1
fi

cleanup_stale_avd_locks() {
  local avd_home="${ANDROID_AVD_HOME:-$HOME/.android/avd}"
  local avd_dir="$avd_home/${AVD_NAME}.avd"
  if [[ -d "$avd_dir" ]]; then
    find "$avd_dir" -maxdepth 1 \( -name '*.lock' -o -name '*.lock/' \) -exec rm -rf {} + 2>/dev/null || true
  fi
}

ensure_avd() {
  if avdmanager list avd | grep -q "Name: ${AVD_NAME}$"; then
    return
  fi

  if ! command -v avdmanager >/dev/null 2>&1; then
    echo "avdmanager nicht gefunden; kann AVD nicht automatisch erstellen."
    exit 1
  fi

  echo "Erstelle AVD ${AVD_NAME} (${ANDROID_IMAGE})..."
  local out
  if ! out="$(echo "no" | avdmanager create avd \
    --name "$AVD_NAME" \
    --package "$ANDROID_IMAGE" \
    --abi "$ANDROID_IMAGE_VENDOR/$ANDROID_ABI" 2>&1)"; then
    if echo "$out" | grep -qi "already exists"; then
      echo "AVD ${AVD_NAME} existiert bereits; fahre fort."
    else
      echo "$out"
      exit 1
    fi
  fi
}

ensure_avd
cleanup_stale_avd_locks

if ! adb devices | grep -q "$EMULATOR_SERIAL"; then
  echo "Starte Emulator $AVD_NAME..."
  # shellcheck disable=SC2086
  nohup emulator -avd "$AVD_NAME" $EMULATOR_ARGS >/tmp/ananta-android-emulator.log 2>&1 &
fi

echo "Warte auf Emulator $EMULATOR_SERIAL..."
adb wait-for-device
adb -s "$EMULATOR_SERIAL" shell getprop sys.boot_completed | tr -d '\r' | grep -q "1" || true
until adb -s "$EMULATOR_SERIAL" shell getprop sys.boot_completed | tr -d '\r' | grep -q "1"; do
  sleep 2
done

for port in $HOST_PORTS; do
  adb -s "$EMULATOR_SERIAL" reverse "tcp:$port" "tcp:$port" >/dev/null
done

cd "$FRONTEND_DIR"
npm run android:prepare

cd "$ANDROID_DIR"
./gradlew \
  :app:connectedDebugAndroidTest \
  "-Pandroid.testInstrumentationRunnerArguments.ananta.e2e.username=$USERNAME" \
  "-Pandroid.testInstrumentationRunnerArguments.ananta.e2e.password=$PASSWORD" \
  -Pandroid.testInstrumentationRunnerArguments.class=com.ananta.mobile.LiveTerminalAndroidE2ETest
