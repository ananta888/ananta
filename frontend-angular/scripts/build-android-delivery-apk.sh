#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend-angular"
ANDROID_DIR="$FRONTEND_DIR/android"
ASSET_DIR="$ANDROID_DIR/app/src/main/assets/proot-seed"
VOXTRAL_RUNNER_ASSET_DIR="$ANDROID_DIR/app/src/main/assets/voxtral-runner"
APK_SOURCE="$ANDROID_DIR/app/build/outputs/apk/debug/app-debug.apk"
APK_OUTPUT="${ANANTA_DELIVERY_APK_OUTPUT:-$ANDROID_DIR/app/build/outputs/apk/debug/ananta-delivery-proot-voxtral-debug.apk}"
PACKAGE_NAME="${ANANTA_ANDROID_PACKAGE:-com.ananta.mobile}"
DEVICE_SERIAL="${ANANTA_ANDROID_SERIAL:-}"
ADB_CONNECT="${ANANTA_ADB_CONNECT:-}"
EXPORT_ANDROID_SEED="${ANANTA_EXPORT_ANDROID_SEED:-0}"
BUILD_VOXTRAL_RUNNER="${ANANTA_BUILD_VOXTRAL_RUNNER:-0}"
REPACK_DIR="${ANANTA_SEED_REPACK_DIR:-$ROOT_DIR/.artifacts/proot-seed-repack}"
VOXTRAL_RUNNER_BUILD_DIR="${ANANTA_VOXTRAL_RUNNER_BUILD_DIR:-$ROOT_DIR/.artifacts/voxtral-runner-build}"
VOXTRAL_REALTIME_SOURCE_URL="${ANANTA_VOXTRAL_REALTIME_SOURCE_URL:-https://github.com/andrijdavid/voxtral.cpp/archive/7deef66c8ee473d3ceffc57fb0cd17977eeebca9.tar.gz}"
GGML_SOURCE_URL="${ANANTA_GGML_SOURCE_URL:-https://github.com/ggml-org/ggml/archive/5cecdad692d868e28dbd2f7c468504770108f30c.tar.gz}"

log() {
  printf '[ananta-delivery-apk] %s\n' "$*"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 2
  fi
}

adb_cmd() {
  if [[ -n "$DEVICE_SERIAL" ]]; then
    adb -s "$DEVICE_SERIAL" "$@"
  else
    adb "$@"
  fi
}

ensure_aapt2_override() {
  if [[ -n "${ANDROID_AAPT2_OVERRIDE:-}" ]]; then
    return
  fi
  if [[ -x /tmp/aapt2 ]]; then
    ANDROID_AAPT2_OVERRIDE=/tmp/aapt2
    return
  fi
  local candidate
  candidate="$(find "$HOME/.gradle/caches" /root/.gradle/caches -type f -path '*aapt2-*-linux/aapt2' 2>/dev/null | head -n 1 || true)"
  if [[ -n "$candidate" ]] && command -v qemu-x86_64 >/dev/null 2>&1; then
    cat >/tmp/aapt2 <<EOF
#!/usr/bin/env sh
export QEMU_LD_PREFIX=/usr/x86_64-linux-gnu
exec qemu-x86_64 "$candidate" "\$@"
EOF
    chmod +x /tmp/aapt2
    ANDROID_AAPT2_OVERRIDE=/tmp/aapt2
  fi
}

verify_seed_assets() {
  local missing=0
  for file in \
    "$ASSET_DIR/ubuntu-rootfs.tar.xz" \
    "$ASSET_DIR/ubuntu-rootfs.version" \
    "$ASSET_DIR/ananta-workspace.tar.xz" \
    "$ASSET_DIR/ananta-workspace.version"; do
    if [[ ! -s "$file" ]]; then
      printf 'Missing seed asset: %s\n' "$file" >&2
      missing=1
    fi
  done
  if [[ "$missing" -ne 0 ]]; then
    printf 'Provide seed assets or run with ANANTA_EXPORT_ANDROID_SEED=1 and a prepared Android app sandbox.\n' >&2
    exit 3
  fi
  xz -t "$ASSET_DIR/ubuntu-rootfs.tar.xz"
  xz -t "$ASSET_DIR/ananta-workspace.tar.xz"
}

verify_voxtral_runner_assets() {
  local missing=0
  for file in \
    "$VOXTRAL_RUNNER_ASSET_DIR/voxtral-realtime" \
    "$VOXTRAL_RUNNER_ASSET_DIR/libvoxtral_lib.so" \
    "$VOXTRAL_RUNNER_ASSET_DIR/libggml.so.0" \
    "$VOXTRAL_RUNNER_ASSET_DIR/libggml-base.so.0" \
    "$VOXTRAL_RUNNER_ASSET_DIR/libggml-cpu.so.0" \
    "$VOXTRAL_RUNNER_ASSET_DIR/voxtral-runner.sha256"; do
    if [[ ! -s "$file" ]]; then
      printf 'Missing Voxtral runner asset: %s\n' "$file" >&2
      missing=1
    fi
  done
  if [[ "$missing" -ne 0 ]]; then
    printf 'Run with ANANTA_BUILD_VOXTRAL_RUNNER=1 to build the bundled Voxtral runner assets.\n' >&2
    exit 5
  fi
}

build_voxtral_runner_assets() {
  require_cmd curl
  require_cmd tar
  require_cmd cmake
  require_cmd sha256sum

  log "Building bundled Voxtral realtime runner"
  rm -rf "$VOXTRAL_RUNNER_BUILD_DIR"
  mkdir -p "$VOXTRAL_RUNNER_BUILD_DIR" "$VOXTRAL_RUNNER_ASSET_DIR"
  (
    cd "$VOXTRAL_RUNNER_BUILD_DIR"
    curl -L --fail -o voxtral.cpp.tar.gz "$VOXTRAL_REALTIME_SOURCE_URL"
    curl -L --fail -o ggml.tar.gz "$GGML_SOURCE_URL"
    tar -xzf voxtral.cpp.tar.gz
    mv voxtral.cpp-* voxtral.cpp
    tar -xzf ggml.tar.gz
    rm -rf voxtral.cpp/ggml
    mv ggml-* voxtral.cpp/ggml
    PREFIX="${PREFIX:-/data/data/com.termux/files/usr}" cmake -B voxtral.cpp/build \
      -DCMAKE_BUILD_TYPE=Release \
      -DVOXTRAL_NATIVE_OPT=OFF \
      -DVOXTRAL_AUTO_DETECT_BLAS=OFF \
      -DVOXTRAL_AUTO_DETECT_CUDA=OFF \
      -DVOXTRAL_AUTO_DETECT_VULKAN=OFF \
      -DGGML_OPENMP=OFF \
      voxtral.cpp
    PREFIX="${PREFIX:-/data/data/com.termux/files/usr}" cmake --build voxtral.cpp/build -j2 --target voxtral
  )

  local build="$VOXTRAL_RUNNER_BUILD_DIR/voxtral.cpp/build"
  local runner=""
  for candidate in "$build/voxtral" "$build/bin/voxtral"; do
    if [[ -x "$candidate" ]]; then
      runner="$candidate"
      break
    fi
  done
  if [[ -z "$runner" ]]; then
    printf 'Voxtral runner binary not found after build.\n' >&2
    exit 6
  fi

  rm -rf "$VOXTRAL_RUNNER_ASSET_DIR"
  mkdir -p "$VOXTRAL_RUNNER_ASSET_DIR"
  cp "$runner" "$VOXTRAL_RUNNER_ASSET_DIR/voxtral-realtime"
  cp "$build/libvoxtral_lib.so" "$VOXTRAL_RUNNER_ASSET_DIR/libvoxtral_lib.so"
  local ggml="$build/ggml/src"
  for file in \
    libggml.so libggml.so.0 libggml.so.0.9.6 \
    libggml-base.so libggml-base.so.0 libggml-base.so.0.9.6 \
    libggml-cpu.so libggml-cpu.so.0 libggml-cpu.so.0.9.6; do
    cp -L "$ggml/$file" "$VOXTRAL_RUNNER_ASSET_DIR/$file"
  done
  chmod 755 "$VOXTRAL_RUNNER_ASSET_DIR/voxtral-realtime"
  chmod 644 "$VOXTRAL_RUNNER_ASSET_DIR"/*.so*
  ( cd "$VOXTRAL_RUNNER_ASSET_DIR" && sha256sum voxtral-realtime libvoxtral_lib.so libggml*.so* > voxtral-runner.sha256 )
  verify_voxtral_runner_assets
}

device_shell() {
  adb_cmd shell run-as "$PACKAGE_NAME" sh -s
}

export_seed_from_android() {
  require_cmd adb
  require_cmd tar
  require_cmd xz

  if [[ -n "$ADB_CONNECT" ]]; then
    log "Connecting adb endpoint $ADB_CONNECT"
    adb connect "$ADB_CONNECT"
  fi

  mkdir -p "$ASSET_DIR" "$REPACK_DIR"
  log "Sanitizing app-sandbox Proot rootfs before export"
  device_shell <<EOS
set -e
BASE=/data/data/$PACKAGE_NAME/files
ROOT="\$BASE/proot-runtime/distros/ubuntu/rootfs/ubuntu-questing-aarch64"
DPKG="\$ROOT/var/lib/dpkg"
if [ -d "\$DPKG" ]; then
  if [ -f "\$DPKG/status-new" ]; then cp "\$DPKG/status-new" "\$DPKG/status"; fi
  if [ -f "\$DPKG/status" ]; then
    awk '
      BEGIN { in_pkg=0 }
      /^Package: libgomp1$/ { in_pkg=1; print; next }
      in_pkg && /^Status:/ { print "Status: install ok installed"; next }
      { print }
      in_pkg && /^$/ { in_pkg=0 }
    ' "\$DPKG/status" > "\$DPKG/status.fixed"
    mv "\$DPKG/status.fixed" "\$DPKG/status"
  fi
  rm -f "\$DPKG/status-new" "\$DPKG/updates"/* "\$DPKG/lock" "\$DPKG/lock-frontend" 2>/dev/null || true
  touch "\$DPKG/lock" "\$DPKG/lock-frontend"
fi
rm -rf "\$ROOT/root/.cache/pip" "\$ROOT/tmp"/* "\$ROOT/var/cache/apt/archives"/*.deb "\$ROOT/var/cache/apt/archives/partial"/* 2>/dev/null || true
rm -f "\$ROOT/usr/local/bin/opencode" "\$ROOT/usr/bin/opencode" "\$ROOT/home/ananta/.local/bin/opencode" "\$ROOT/root/.local/bin/opencode" 2>/dev/null || true
for d in data dev proc sys tmp run; do mkdir -p "\$ROOT/\$d"; chmod u+rwx "\$ROOT/\$d" 2>/dev/null || true; done
find "\$ROOT" \( -name '.l2s*' -o -name '*.l2s*' \) -delete 2>/dev/null || true
test -e "\$BASE/ananta/agent/ai_agent.py"
test -e "\$ROOT/usr/bin/python3"
test -e "\$ROOT/usr/local/bin/ananta"
test -e "\$ROOT/usr/local/bin/ananta-worker"
EOS

  log "Exporting rootfs stream from Android"
  rm -rf "$REPACK_DIR"
  mkdir -p "$REPACK_DIR"
  adb_cmd exec-out run-as "$PACKAGE_NAME" sh -c "cd /data/data/$PACKAGE_NAME/files/proot-runtime/distros/ubuntu/rootfs && tar -cf - ubuntu-questing-aarch64" \
    | xz -T0 -6 > "$ASSET_DIR/ubuntu-rootfs.raw-android.tar.xz"

  log "Repacking rootfs with GNU tar for reproducible Android extraction"
  ( cd "$REPACK_DIR" && tar -xJf "$ASSET_DIR/ubuntu-rootfs.raw-android.tar.xz" ) || true
  local rootfs="$REPACK_DIR/ubuntu-questing-aarch64"
  for required in usr/bin/python3 usr/bin/git usr/bin/curl usr/local/bin/ananta usr/local/bin/ananta-worker; do
    if [[ ! -e "$rootfs/$required" ]]; then
      printf 'Exported rootfs is missing %s\n' "$required" >&2
      exit 4
    fi
  done
  rm -f "$rootfs/usr/local/bin/opencode" "$rootfs/usr/bin/opencode" "$rootfs/home/ananta/.local/bin/opencode" "$rootfs/root/.local/bin/opencode"
  if [[ -e "$rootfs/usr/local/bin/opencode" || -e "$rootfs/usr/bin/opencode" || -e "$rootfs/home/ananta/.local/bin/opencode" || -e "$rootfs/root/.local/bin/opencode" ]]; then
    printf 'Bundled rootfs must not include opencode binary.\n' >&2
    exit 4
  fi
  rm -f "$rootfs/usr/bin/perlbug" "$rootfs/usr/bin/perlthanks"
  ln -s /usr/bin/perl "$rootfs/usr/bin/perlbug"
  ln -s /usr/bin/perl "$rootfs/usr/bin/perlthanks"
  for d in data dev proc sys tmp run; do mkdir -p "$rootfs/$d"; chmod 755 "$rootfs/$d"; done
  find "$rootfs" \( -name '.l2s*' -o -name '*.l2s*' \) -delete 2>/dev/null || true
  for target in "$rootfs/usr/local/bin/ananta" "$rootfs/root/.local/bin/ananta" "$rootfs/home/ananta/.local/bin/ananta"; do
    if [[ -e "$target" ]]; then
      cat >"$target" <<'EOF'
#!/bin/sh
ANANTA_WORKSPACE=/data/user/0/com.ananta.mobile/files/ananta
DATA_DIR=${DATA_DIR:-/data/user/0/com.ananta.mobile/files/ananta-data}
export DATA_DIR
PYTHONPATH="$ANANTA_WORKSPACE:${PYTHONPATH:-}" exec python3 -m agent.cli.main "$@"
EOF
      chmod 755 "$target"
    fi
  done
  for target in "$rootfs/usr/local/bin/ananta-worker" "$rootfs/root/.local/bin/ananta-worker" "$rootfs/home/ananta/.local/bin/ananta-worker"; do
    if [[ -e "$target" ]]; then
      cat >"$target" <<'EOF'
#!/bin/sh
ANANTA_WORKSPACE=/data/user/0/com.ananta.mobile/files/ananta
export ROLE=${ROLE:-worker}
export AGENT_NAME=${AGENT_NAME:-android-worker}
export PORT=${PORT:-5001}
export HUB_URL=${HUB_URL:-http://127.0.0.1:5000}
export AGENT_URL=${AGENT_URL:-http://127.0.0.1:${PORT}}
export DATA_DIR=${DATA_DIR:-/data/user/0/com.ananta.mobile/files/ananta-data}
PYTHONPATH="$ANANTA_WORKSPACE:${PYTHONPATH:-}" exec python3 -m agent.ai_agent "$@"
EOF
      chmod 755 "$target"
    fi
  done
  ( cd "$REPACK_DIR" && tar --format=gnu --numeric-owner --owner=0 --group=0 -cf - ubuntu-questing-aarch64 ) \
    | xz -T0 -6 > "$ASSET_DIR/ubuntu-rootfs.tar.xz"
  rm -f "$ASSET_DIR/ubuntu-rootfs.raw-android.tar.xz"
  printf 'android-ubuntu-base-%s\n' "$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || printf unknown)" > "$ASSET_DIR/ubuntu-rootfs.version"

  log "Exporting Ananta workspace seed"
  adb_cmd exec-out run-as "$PACKAGE_NAME" sh -c "cd /data/data/$PACKAGE_NAME/files/ananta && tar -cf - ." \
    | xz -T0 -6 > "$ASSET_DIR/ananta-workspace.tar.xz"
  printf 'ananta-workspace-%s\n' "$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || printf unknown)" > "$ASSET_DIR/ananta-workspace.version"

  verify_seed_assets
}

build_apk() {
  require_cmd npm
  require_cmd unzip
  require_cmd sha256sum

  log "Building Angular assets and syncing Capacitor"
  ( cd "$FRONTEND_DIR" && npm run android:prepare )

  verify_seed_assets
  verify_voxtral_runner_assets
  ensure_aapt2_override

  log "Building Android debug APK"
  local gradle_args=(--no-daemon :app:assembleDebug)
  if [[ -n "${ANDROID_AAPT2_OVERRIDE:-}" ]]; then
    gradle_args=(-Pandroid.aapt2FromMavenOverride="$ANDROID_AAPT2_OVERRIDE" "${gradle_args[@]}")
  fi
  ( cd "$ANDROID_DIR" && ./gradlew "${gradle_args[@]}" )

  mkdir -p "$(dirname "$APK_OUTPUT")"
  cp "$APK_SOURCE" "$APK_OUTPUT"
  log "APK: $APK_OUTPUT"
  sha256sum "$APK_OUTPUT"
  ls -lh "$APK_OUTPUT"
  unzip -l "$APK_OUTPUT" | grep 'assets/proot-seed/' >/dev/null
  unzip -l "$APK_OUTPUT" | grep 'assets/voxtral-runner/voxtral-realtime' >/dev/null
}

if [[ "$EXPORT_ANDROID_SEED" == "1" ]]; then
  export_seed_from_android
fi
if [[ "$BUILD_VOXTRAL_RUNNER" == "1" ]]; then
  build_voxtral_runner_assets
fi

build_apk
