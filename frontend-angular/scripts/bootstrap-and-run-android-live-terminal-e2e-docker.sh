#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUTO_SHUTDOWN_STACK="${ANANTA_ANDROID_AUTO_SHUTDOWN_STACK:-1}"
AVD_NAME="${ANANTA_ANDROID_AVD_NAME:-ananta-api35}"
EMULATOR_SERIAL="${ANANTA_ANDROID_EMULATOR_SERIAL:-emulator-5554}"
HOST_EMULATOR_ARGS="${ANANTA_HOST_ANDROID_EMULATOR_ARGS:--no-window -no-audio -no-boot-anim -no-snapshot-load -no-snapshot-save -gpu swiftshader_indirect -memory 1536 -cores 2 -no-metrics}"

prepare_docker_config() {
  export DOCKER_CONFIG="${ANANTA_DOCKER_CONFIG_DIR:-/tmp/ananta-docker-config}"
  export DOCKER_AUTH_CONFIG='{"auths":{}}'
  export DOCKER_BUILDKIT=1
  export COMPOSE_DOCKER_CLI_BUILD=1

  mkdir -p "$DOCKER_CONFIG"
  cat > "$DOCKER_CONFIG/config.json" <<'JSON'
{
  "auths": {}
}
JSON
}

prepare_docker_credential_stub() {
  local helper_dir="${ANANTA_DOCKER_HELPER_DIR:-/tmp/ananta-docker-helpers}"
  mkdir -p "$helper_dir"
  cat > "$helper_dir/docker-credential-secretservice" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
case "$cmd" in
  get)
    echo '{"Username":"","Secret":""}'
    ;;
  list)
    echo '{}'
    ;;
  store|erase)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
SCRIPT
  chmod +x "$helper_dir/docker-credential-secretservice"
  export PATH="$helper_dir:$PATH"
}

start_host_emulator() {
  if [[ -x "/tmp/android-sdk/platform-tools/adb" && -x "/tmp/android-sdk/emulator/emulator" ]]; then
    export PATH="/tmp/android-sdk/platform-tools:/tmp/android-sdk/emulator:$PATH"
  fi

  if ! command -v adb >/dev/null 2>&1 || ! command -v emulator >/dev/null 2>&1; then
    echo "Fehlt adb/emulator auf dem Host. Bitte Android SDK Platform-Tools + Emulator installieren."
    exit 1
  fi

  if [[ ! -w /dev/kvm ]]; then
    echo "KVM nicht nutzbar fuer aktuellen Benutzer (/dev/kvm). Bitte: sudo gpasswd -a $USER kvm und neu anmelden."
    exit 1
  fi

  if ! adb start-server >/dev/null 2>&1; then
    echo "adb start-server fehlgeschlagen. Bitte Host-ADB-Rechte/Umgebung pruefen."
    exit 1
  fi

  if ! adb devices | grep -q "$EMULATOR_SERIAL"; then
    echo "Starte Host-Emulator $AVD_NAME..."
    # shellcheck disable=SC2086
    nohup emulator -avd "$AVD_NAME" $HOST_EMULATOR_ARGS >/tmp/ananta-host-android-emulator.log 2>&1 &
  fi

  echo "Warte auf Host-Emulator $EMULATOR_SERIAL..."
  adb wait-for-device
  until adb -s "$EMULATOR_SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' | grep -q "1"; do
    sleep 2
  done
}


start_stack() {
  export ANANTA_USE_WSL_VULKAN="${ANANTA_USE_WSL_VULKAN:-0}"
  "$ROOT_DIR/scripts/compose-test-stack.sh" down || true
  "$ROOT_DIR/scripts/compose-test-stack.sh" up-live
}

stop_stack() {
  "$ROOT_DIR/scripts/compose-test-stack.sh" down || true
}

main() {
  prepare_docker_config
  prepare_docker_credential_stub
  start_host_emulator
  trap 'if [[ "$AUTO_SHUTDOWN_STACK" == "1" ]]; then stop_stack; fi' EXIT

  start_stack
  "$ROOT_DIR/scripts/compose-test-stack.sh" run-android-e2e
}

main "$@"
