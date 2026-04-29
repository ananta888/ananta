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

if ! command -v adb >/dev/null 2>&1; then
  echo "adb nicht gefunden. Android SDK Platform-Tools installieren."
  exit 1
fi

if ! command -v emulator >/dev/null 2>&1; then
  echo "emulator binary nicht gefunden. Android SDK Emulator installieren."
  exit 1
fi

if ! adb devices | grep -q "$EMULATOR_SERIAL"; then
  echo "Starte Emulator $AVD_NAME..."
  nohup emulator -avd "$AVD_NAME" -no-snapshot-load -netdelay none -netspeed full >/tmp/ananta-android-emulator.log 2>&1 &
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
