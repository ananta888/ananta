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
SKIP_EMULATOR_START="${ANANTA_ANDROID_SKIP_EMULATOR_START:-0}"

# Best-effort host fallbacks for local SDK/JDK installs used in this project.
if [[ -x "/tmp/ananta-toolchain/jdk-17/bin/java" && -z "${JAVA_HOME:-}" ]]; then
  export JAVA_HOME="/tmp/ananta-toolchain/jdk-17"
  export PATH="$JAVA_HOME/bin:$PATH"
fi
if [[ -d "/tmp/android-sdk" ]]; then
  export PATH="/tmp/android-sdk/cmdline-tools/latest/bin:/tmp/android-sdk/platform-tools:/tmp/android-sdk/emulator:$PATH"
fi

if ! command -v adb >/dev/null 2>&1; then
  echo "adb nicht gefunden. Android SDK Platform-Tools installieren."
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

if [[ "$SKIP_EMULATOR_START" != "1" ]]; then
  if ! command -v emulator >/dev/null 2>&1; then
    echo "emulator binary nicht gefunden. Android SDK Emulator installieren."
    exit 1
  fi
  ensure_avd
  cleanup_stale_avd_locks

  if ! adb devices | grep -q "$EMULATOR_SERIAL"; then
    echo "Starte Emulator $AVD_NAME..."
    # shellcheck disable=SC2086
    nohup emulator -avd "$AVD_NAME" $EMULATOR_ARGS >/tmp/ananta-android-emulator.log 2>&1 &
  fi
fi

echo "Warte auf Android-Device $EMULATOR_SERIAL..."
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
AAPT2_ARG=()
if [[ -x /tmp/aapt2 ]]; then
  AAPT2_ARG=(-Pandroid.aapt2FromMavenOverride=/tmp/aapt2)
fi
./gradlew \
  :app:assembleDebug \
  :app:assembleDebugAndroidTest \
  "${AAPT2_ARG[@]}"

APP_APK="$ANDROID_DIR/app/build/outputs/apk/debug/app-debug.apk"
TEST_APK="$ANDROID_DIR/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk"
if [[ ! -f "$APP_APK" || ! -f "$TEST_APK" ]]; then
  echo "APK output fehlt (app oder androidTest)."
  exit 1
fi

install_output="$(adb -s "$EMULATOR_SERIAL" install -r "$APP_APK" 2>&1 | cat)"
if ! echo "$install_output" | grep -q "Success"; then
  echo "$install_output"
  if echo "$install_output" | grep -q "INSTALL_FAILED_UPDATE_INCOMPATIBLE"; then
    echo "APK-Signatur passt nicht zur installierten App."
    echo "Hinweis: Fuer persistente lokale Modelle (z. B. Voxtral) immer denselben Debug-Keystore nutzen."
    echo "Setze ggf. -PanantaDebugKeystorePath=<pfad> oder ANANTA_DEBUG_KEYSTORE_PATH."
  fi
  exit 1
fi

adb -s "$EMULATOR_SERIAL" install -r -t "$TEST_APK" >/dev/null
INSTRUMENTATION_OUTPUT="$(adb -s "$EMULATOR_SERIAL" shell am instrument -w -r \
  -e ananta.e2e.username "$USERNAME" \
  -e ananta.e2e.password "$PASSWORD" \
  -e class com.ananta.mobile.LiveTerminalAndroidE2ETest \
  com.ananta.mobile.test/androidx.test.runner.AndroidJUnitRunner | cat)"
echo "$INSTRUMENTATION_OUTPUT"
if echo "$INSTRUMENTATION_OUTPUT" | grep -q "FAILURES!!!"; then
  echo "Instrumentation-Tests fehlgeschlagen."
  exit 1
fi
