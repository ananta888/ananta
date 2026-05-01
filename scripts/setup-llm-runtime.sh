#!/usr/bin/env bash
# setup-llm-runtime.sh — Download and set up local LLM runtime for on-device inference.
#
# Installs:
#   - llama.cpp server (pre-built Ubuntu ARM64 binary)
#   - SmolLM2-135M-Instruct GGUF model (Q8_0, ~139MB)
#   - opencode CLI (pre-built ARM64)
#
# All binaries are installed to $ANANTA_LLM_HOME (default: $HOME/.ananta/llm-runtime).
# The script is idempotent — re-running skips already installed components.
#
# Usage:
#   ./scripts/setup-llm-runtime.sh          # install everything
#   ./scripts/setup-llm-runtime.sh --check  # verify installation only

set -euo pipefail

# --- Configuration (pinned versions + checksums) ---

LLAMA_VERSION="b8994"
LLAMA_TARBALL="llama-${LLAMA_VERSION}-bin-ubuntu-arm64.tar.gz"
LLAMA_URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_VERSION}/${LLAMA_TARBALL}"
LLAMA_SHA256="50e857be7a77a2a591550834a590f01b8189f5fa6f84290db749a371e4d61287"

MODEL_NAME="SmolLM2-135M-Instruct-Q8_0.gguf"
MODEL_FILE="smollm2-135m-q8.gguf"
MODEL_URL="https://huggingface.co/bartowski/SmolLM2-135M-Instruct-GGUF/resolve/main/${MODEL_NAME}"
MODEL_SHA256="5a1395716f7913741cc51d98581b9b1228d80987a9f7d3664106742eb06bba83"

OPENCODE_VERSION="v0.0.55"
OPENCODE_TARBALL="opencode-linux-arm64.tar.gz"
OPENCODE_URL="https://github.com/opencode-ai/opencode/releases/download/${OPENCODE_VERSION}/${OPENCODE_TARBALL}"
OPENCODE_SHA256="530eb136fdfc9eadef96aa2250bbdb9210e9f7cc1bd4f733d332ca9dd61d22e0"

ANANTA_LLM_HOME="${ANANTA_LLM_HOME:-$HOME/.ananta/llm-runtime}"

# --- Helper functions ---

info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*" >&2; }
error() { echo "[ERROR] $*" >&2; exit 1; }

check_arch() {
    local arch
    arch="$(uname -m)"
    if [ "$arch" != "aarch64" ] && [ "$arch" != "arm64" ]; then
        error "This script requires aarch64/arm64 architecture (detected: $arch)"
    fi
}

verify_sha256() {
    local file="$1" expected="$2"
    local actual
    actual="$(sha256sum "$file" | awk '{print $1}')"
    if [ "$actual" != "$expected" ]; then
        error "SHA256 mismatch for $file: expected $expected, got $actual"
    fi
}

download_if_missing() {
    local url="$1" dest="$2" sha256="$3" label="$4"
    if [ -f "$dest" ]; then
        info "$label already downloaded, verifying checksum..."
        verify_sha256 "$dest" "$sha256"
        info "$label checksum OK"
        return 0
    fi
    info "Downloading $label..."
    curl -L --progress-bar "$url" -o "$dest"
    verify_sha256 "$dest" "$sha256"
    info "$label downloaded and verified"
}

# --- Installation functions ---

install_llama() {
    local llama_dir="$ANANTA_LLM_HOME/llama-cpp"
    local marker="$llama_dir/.version-${LLAMA_VERSION}"

    if [ -f "$marker" ] && [ -x "$llama_dir/llama-server" ]; then
        info "llama.cpp $LLAMA_VERSION already installed"
        return 0
    fi

    info "Installing llama.cpp $LLAMA_VERSION..."
    mkdir -p "$llama_dir"
    local tarball="$ANANTA_LLM_HOME/downloads/$LLAMA_TARBALL"
    mkdir -p "$ANANTA_LLM_HOME/downloads"
    download_if_missing "$LLAMA_URL" "$tarball" "$LLAMA_SHA256" "llama.cpp $LLAMA_VERSION"

    tar xzf "$tarball" -C "$llama_dir" --strip-components=1
    chmod +x "$llama_dir/llama-server" "$llama_dir/llama-cli" 2>/dev/null || true
    touch "$marker"
    info "llama.cpp installed to $llama_dir"
}

install_model() {
    local model_path="$ANANTA_LLM_HOME/models/$MODEL_FILE"

    if [ -f "$model_path" ]; then
        info "Model $MODEL_FILE already present, verifying..."
        verify_sha256 "$model_path" "$MODEL_SHA256"
        info "Model checksum OK"
        return 0
    fi

    info "Downloading model $MODEL_FILE (~139MB)..."
    mkdir -p "$ANANTA_LLM_HOME/models"
    curl -L --progress-bar "$MODEL_URL" -o "$model_path"
    verify_sha256 "$model_path" "$MODEL_SHA256"
    info "Model downloaded to $model_path"
}

install_opencode() {
    local opencode_dir="$ANANTA_LLM_HOME/opencode"
    local marker="$opencode_dir/.version-${OPENCODE_VERSION}"

    if [ -f "$marker" ] && [ -x "$opencode_dir/opencode" ]; then
        info "opencode $OPENCODE_VERSION already installed"
        return 0
    fi

    info "Installing opencode $OPENCODE_VERSION..."
    mkdir -p "$opencode_dir"
    local tarball="$ANANTA_LLM_HOME/downloads/$OPENCODE_TARBALL"
    mkdir -p "$ANANTA_LLM_HOME/downloads"
    download_if_missing "$OPENCODE_URL" "$tarball" "$OPENCODE_SHA256" "opencode $OPENCODE_VERSION"

    tar xzf "$tarball" -C "$opencode_dir"
    chmod +x "$opencode_dir/opencode"
    touch "$marker"
    info "opencode installed to $opencode_dir"
}

# --- Preflight / check function ---

preflight_check() {
    local ok=true
    local llama_dir="$ANANTA_LLM_HOME/llama-cpp"
    local opencode_dir="$ANANTA_LLM_HOME/opencode"
    local model_path="$ANANTA_LLM_HOME/models/$MODEL_FILE"

    echo "=== Ananta LLM Runtime Preflight Check ==="
    echo "ANANTA_LLM_HOME: $ANANTA_LLM_HOME"
    echo ""

    # llama-server
    if [ -x "$llama_dir/llama-server" ]; then
        local ver
        ver="$(cd "$llama_dir" && LD_LIBRARY_PATH="$llama_dir:${LD_LIBRARY_PATH:-}" "$llama_dir/llama-server" --version 2>&1 | grep 'version:' | head -1 || echo "unknown")"
        echo "[OK] llama-server: $ver"
    else
        echo "[MISSING] llama-server not found at $llama_dir/llama-server"
        ok=false
    fi

    # Model
    if [ -f "$model_path" ]; then
        local size
        size="$(du -h "$model_path" | cut -f1)"
        echo "[OK] Model: $MODEL_FILE ($size)"
    else
        echo "[MISSING] Model not found at $model_path"
        ok=false
    fi

    # opencode
    if [ -x "$opencode_dir/opencode" ]; then
        local ocver
        ocver="$("$opencode_dir/opencode" --version 2>&1 | tail -1)"
        echo "[OK] opencode: $ocver"
    else
        echo "[MISSING] opencode not found at $opencode_dir/opencode"
        ok=false
    fi

    # llama-server running?
    echo ""
    if curl -s --max-time 2 http://127.0.0.1:8081/health >/dev/null 2>&1; then
        local n_ctx
        n_ctx="$(curl -s http://127.0.0.1:8081/props | python3 -c "import json,sys;print(json.load(sys.stdin).get('default_generation_settings',{}).get('n_ctx','?'))" 2>/dev/null || echo "?")"
        echo "[OK] llama-server running on :8081 (n_ctx=$n_ctx)"

        local models
        models="$(curl -s http://127.0.0.1:8081/v1/models | python3 -c "import json,sys;[print('     -', m['id']) for m in json.load(sys.stdin).get('data',[])]" 2>/dev/null || echo "     (error reading models)")"
        echo "$models"
    else
        echo "[INFO] llama-server not running on :8081 (start with: $0 --start-server)"
    fi

    echo ""
    if $ok; then
        echo "=== All components installed ==="
    else
        echo "=== Some components missing — run: $0 ==="
    fi

    $ok
}

# --- Server start helper ---

start_server() {
    local llama_dir="$ANANTA_LLM_HOME/llama-cpp"
    local model_path="$ANANTA_LLM_HOME/models/$MODEL_FILE"
    local port="${ANANTA_LLM_PORT:-8081}"
    local ctx="${ANANTA_LLM_CTX:-16384}"

    if ! [ -x "$llama_dir/llama-server" ]; then
        error "llama-server not installed. Run: $0"
    fi
    if ! [ -f "$model_path" ]; then
        error "Model not found. Run: $0"
    fi

    info "Starting llama-server on port $port (ctx=$ctx)..."
    cd "$llama_dir"
    exec env \
        LD_LIBRARY_PATH="$llama_dir:${LD_LIBRARY_PATH:-}" \
        "$llama_dir/llama-server" \
        -m "$model_path" \
        --host 127.0.0.1 \
        --port "$port" \
        -c "$ctx" \
        -np 1 \
        -ngl 0 \
        --override-kv "llama.context_length=int:$ctx"
}

# --- Main ---

main() {
    case "${1:-}" in
        --check)
            preflight_check
            ;;
        --start-server)
            start_server
            ;;
        *)
            check_arch
            info "Installing Ananta LLM runtime to $ANANTA_LLM_HOME"
            echo ""
            install_llama
            echo ""
            install_model
            echo ""
            install_opencode
            echo ""
            info "Installation complete."
            echo ""
            preflight_check
            echo ""
            echo "Next steps:"
            echo "  Start llama-server:  $0 --start-server"
            echo "  Use opencode:        LOCAL_ENDPOINT=http://127.0.0.1:8081/v1 $ANANTA_LLM_HOME/opencode/opencode"
            echo "  Preflight check:     $0 --check"
            ;;
    esac
}

main "$@"
