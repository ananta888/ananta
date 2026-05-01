#!/usr/bin/env bash
# setup-android-build-env.sh — Set up Android build environment on aarch64 host.
#
# The Android SDK tools (aapt2, cmake, ninja, NDK clang) are x86-64 binaries.
# On an aarch64 host (e.g. Termux proot), they need qemu-x86_64 wrappers.
#
# This script creates those wrappers and verifies the build toolchain.
#
# Prerequisites:
#   - qemu-user installed (provides qemu-x86_64)
#   - x86-64 glibc libs installed (at least libz:amd64)
#   - Android SDK with build-tools, cmake, and NDK installed
#
# Usage:
#   ./scripts/setup-android-build-env.sh          # set up wrappers
#   ./scripts/setup-android-build-env.sh --check  # verify only

set -euo pipefail

ANDROID_SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/android-sdk}}"
QEMU_LD_PREFIX="${QEMU_LD_PREFIX:-/usr/x86_64-linux-gnu}"

info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*" >&2; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# --- Detect installed SDK components ---

detect_cmake_version() {
    local cmake_dir="$ANDROID_SDK/cmake"
    if [ -d "$cmake_dir" ]; then
        ls "$cmake_dir" | sort -V | tail -1
    fi
}

detect_ndk_version() {
    local ndk_dir="$ANDROID_SDK/ndk"
    if [ -d "$ndk_dir" ]; then
        ls "$ndk_dir" | sort -V | tail -1
    fi
}

detect_build_tools_version() {
    local bt_dir="$ANDROID_SDK/build-tools"
    if [ -d "$bt_dir" ]; then
        ls "$bt_dir" | sort -V | tail -1
    fi
}

# --- Wrapper creation ---

create_qemu_wrapper() {
    local real_binary="$1"
    local wrapper_path="$2"
    local label="${3:-$(basename "$wrapper_path")}"

    if [ ! -f "$real_binary" ]; then
        warn "Binary not found: $real_binary (skipping $label)"
        return 1
    fi

    cat > "$wrapper_path" <<WRAPPER
#!/bin/sh
export QEMU_LD_PREFIX="$QEMU_LD_PREFIX"
exec qemu-x86_64 "$real_binary" "\$@"
WRAPPER
    chmod +x "$wrapper_path"
}

setup_aapt2() {
    local bt_version
    bt_version="$(detect_build_tools_version)"
    if [ -z "$bt_version" ]; then
        warn "No build-tools found in $ANDROID_SDK/build-tools/"
        return 1
    fi

    local aapt2_real=""
    # Find the actual aapt2 binary (may be in gradle caches or build-tools)
    for candidate in \
        "$ANDROID_SDK/build-tools/$bt_version/aapt2" \
        "$HOME/.gradle/caches"/*/transforms/*/transformed/aapt2-*/aapt2; do
        if [ -f "$candidate" ]; then
            local filetype
            filetype="$(file -b "$candidate" 2>/dev/null || echo "")"
            if echo "$filetype" | grep -q "x86.64"; then
                aapt2_real="$candidate"
                break
            fi
        fi
    done

    if [ -z "$aapt2_real" ]; then
        warn "Could not find x86-64 aapt2 binary"
        return 1
    fi

    local wrapper="/tmp/aapt2"
    create_qemu_wrapper "$aapt2_real" "$wrapper" "aapt2"
    info "aapt2 wrapper: $wrapper → $aapt2_real"

    # Verify
    if "$wrapper" version >/dev/null 2>&1; then
        info "aapt2 wrapper works: $("$wrapper" version 2>&1 | head -1)"
    else
        warn "aapt2 wrapper created but verification failed"
    fi
}

setup_cmake_ninja() {
    local cmake_version
    cmake_version="$(detect_cmake_version)"
    if [ -z "$cmake_version" ]; then
        warn "No cmake found in $ANDROID_SDK/cmake/"
        return 1
    fi

    local cmake_bin="$ANDROID_SDK/cmake/$cmake_version/bin"
    info "Setting up cmake/ninja symlinks in $cmake_bin"

    # cmake: symlink to native binary
    if command -v cmake >/dev/null 2>&1; then
        local native_cmake
        native_cmake="$(command -v cmake)"
        for tool in cmake cpack ctest; do
            if [ -f "$cmake_bin/$tool" ]; then
                mv "$cmake_bin/$tool" "$cmake_bin/${tool}.x86_backup" 2>/dev/null || true
            fi
            ln -sf "$native_cmake" "$cmake_bin/$tool" 2>/dev/null || true
        done
        info "cmake → $native_cmake ($("$cmake_bin/cmake" --version 2>&1 | head -1))"
    else
        warn "Native cmake not found. Install: apt install cmake"
        return 1
    fi

    # ninja: symlink to native binary
    if command -v ninja >/dev/null 2>&1; then
        local native_ninja
        native_ninja="$(command -v ninja)"
        if [ -f "$cmake_bin/ninja" ]; then
            mv "$cmake_bin/ninja" "$cmake_bin/ninja.x86_backup" 2>/dev/null || true
        fi
        ln -sf "$native_ninja" "$cmake_bin/ninja"
        info "ninja → $native_ninja ($("$cmake_bin/ninja" --version 2>&1))"
    else
        warn "Native ninja not found. Install: apt install ninja-build"
        return 1
    fi
}

setup_ndk_wrappers() {
    local ndk_version
    ndk_version="$(detect_ndk_version)"
    if [ -z "$ndk_version" ]; then
        warn "No NDK found in $ANDROID_SDK/ndk/"
        return 1
    fi

    local ndk_bin="$ANDROID_SDK/ndk/$ndk_version/toolchains/llvm/prebuilt/linux-x86_64/bin"
    if [ ! -d "$ndk_bin" ]; then
        warn "NDK toolchain bin not found: $ndk_bin"
        return 1
    fi

    # Check prerequisite: x86-64 libs
    if [ ! -f "$QEMU_LD_PREFIX/lib/libc.so.6" ]; then
        warn "x86-64 glibc not found at $QEMU_LD_PREFIX. Install: dpkg --add-architecture amd64 && apt install libc6:amd64 libz1:amd64"
        return 1
    fi

    # Test that qemu can run NDK clang
    if ! QEMU_LD_PREFIX="$QEMU_LD_PREFIX" qemu-x86_64 "$ndk_bin/clang" --version >/dev/null 2>&1; then
        warn "NDK clang via qemu failed. May need additional x86-64 libs."
        return 1
    fi

    local clang_version
    clang_version="$(QEMU_LD_PREFIX="$QEMU_LD_PREFIX" qemu-x86_64 "$ndk_bin/clang" --version 2>&1 | head -1)"
    info "NDK clang works via qemu: $clang_version"
    info "NDK wrappers not needed — qemu binfmt or manual invocation handles execution"
}

# --- Preflight check ---

preflight_check() {
    echo "=== Android Build Environment Check ==="
    echo "ANDROID_SDK: $ANDROID_SDK"
    echo "QEMU_LD_PREFIX: $QEMU_LD_PREFIX"
    echo ""

    # Architecture
    local arch
    arch="$(uname -m)"
    echo "Host architecture: $arch"
    if [ "$arch" = "aarch64" ] || [ "$arch" = "arm64" ]; then
        echo "[OK] aarch64 host — qemu wrappers needed for x86-64 SDK tools"
    else
        echo "[OK] $arch host — native SDK tools should work"
        return 0
    fi

    echo ""

    # qemu-x86_64
    if command -v qemu-x86_64 >/dev/null 2>&1; then
        echo "[OK] qemu-x86_64: $(qemu-x86_64 --version 2>&1 | head -1)"
    else
        echo "[MISSING] qemu-x86_64 — install: apt install qemu-user"
    fi

    # x86-64 libs
    if [ -f "$QEMU_LD_PREFIX/lib/libc.so.6" ]; then
        echo "[OK] x86-64 glibc at $QEMU_LD_PREFIX"
    else
        echo "[MISSING] x86-64 glibc — install: dpkg --add-architecture amd64 && apt install libc6:amd64 libz1:amd64"
    fi

    # aapt2
    if [ -x /tmp/aapt2 ]; then
        echo "[OK] aapt2 wrapper: $(/tmp/aapt2 version 2>&1 | head -1)"
    else
        echo "[MISSING] aapt2 wrapper at /tmp/aapt2"
    fi

    # cmake
    local cmake_version
    cmake_version="$(detect_cmake_version)"
    if [ -n "$cmake_version" ] && [ -x "$ANDROID_SDK/cmake/$cmake_version/bin/cmake" ]; then
        echo "[OK] cmake: $("$ANDROID_SDK/cmake/$cmake_version/bin/cmake" --version 2>&1 | head -1)"
    else
        echo "[MISSING] cmake in SDK"
    fi

    # ninja
    if [ -n "$cmake_version" ] && [ -x "$ANDROID_SDK/cmake/$cmake_version/bin/ninja" ]; then
        echo "[OK] ninja: $("$ANDROID_SDK/cmake/$cmake_version/bin/ninja" --version 2>&1)"
    else
        echo "[MISSING] ninja in SDK"
    fi

    # NDK
    local ndk_version
    ndk_version="$(detect_ndk_version)"
    if [ -n "$ndk_version" ]; then
        echo "[OK] NDK: $ndk_version"
    else
        echo "[MISSING] NDK"
    fi

    echo ""
    echo "=== Check complete ==="
}

# --- Main ---

main() {
    case "${1:-}" in
        --check)
            preflight_check
            ;;
        *)
            if [ "$(uname -m)" != "aarch64" ] && [ "$(uname -m)" != "arm64" ]; then
                info "Not an aarch64 host — no qemu wrappers needed"
                exit 0
            fi

            info "Setting up Android build environment on aarch64 host"
            echo ""
            setup_aapt2 || true
            echo ""
            setup_cmake_ninja || true
            echo ""
            setup_ndk_wrappers || true
            echo ""
            preflight_check
            ;;
    esac
}

main "$@"
