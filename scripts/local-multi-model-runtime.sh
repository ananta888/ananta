#!/usr/bin/env bash
# Hub-side operator adapter for the local KAT + LFM + Needle deployment.
set -euo pipefail

RUNTIME_ROOT="${ANANTA_LOCAL_MODEL_ROOT:-/home/krusty/moe-test}"
STATE_DIR="${ANANTA_LOCAL_MODEL_STATE_DIR:-/home/krusty/ananta/data/local-model-runtime}"
KAT_DIR="${ANANTA_KAT_MODEL_DIR:-$RUNTIME_ROOT/colibri/models/kat_coder_v2_5_i4_gs64}"
COLI_DIR="${ANANTA_COLIBRI_DIR:-$RUNTIME_ROOT/colibri/c}"
LFM_MODEL="${ANANTA_LFM_MODEL:-$RUNTIME_ROOT/models/lfm2_5_2_6b/LFM2.5-2.6B-Q8_0.gguf}"
LLAMA_BIN_DIR="${ANANTA_LLAMA_BIN_DIR:-$RUNTIME_ROOT/llama.cpp/build-cuda/bin}"
CUDA_LIB_DIR="${ANANTA_CUDA_LIB_DIR:-$RUNTIME_ROOT/colibri/.cuda-toolkit/lib}"
NEEDLE_PYTHON="${ANANTA_NEEDLE_PYTHON:-$RUNTIME_ROOT/.venv-needle/bin/python}"
NEEDLE_WEIGHTS="${ANANTA_NEEDLE_WEIGHTS:-$RUNTIME_ROOT/models/needle2/needle2.cact}"
NEEDLE_SIDECAR="${ANANTA_NEEDLE_SIDECAR:-/home/krusty/ananta/scripts/needle-candidate-sidecar.py}"
LFM_PORT="${ANANTA_LFM_PORT:-8081}"
KAT_PORT="${ANANTA_KAT_PORT:-8082}"
NEEDLE_PORT="${ANANTA_NEEDLE_PORT:-8083}"
MODEL_BIND_HOST="${ANANTA_LOCAL_MODEL_BIND_HOST:-127.0.0.1}"
MODEL_API_KEY="${ANANTA_LOCAL_MODEL_API_KEY:-}"
NEEDLE_TOKEN="${ANANTA_NEEDLE_TOKEN:-$MODEL_API_KEY}"
LFM_CTX="${ANANTA_LFM_CTX:-32768}"
KAT_CTX="${ANANTA_KAT_CTX:-32768}"
# The original exclusive KAT baseline used 8 GiB.  In the measured shared
# deployment 5 GiB left less than the mandatory reserve, so 4 GiB is the safe
# RTX-3080 default; operators may raise it only after a successful preflight.
KAT_EXPERT_GB="${ANANTA_KAT_EXPERT_GB:-4}"
VRAM_RESERVE_MIB="${ANANTA_LOCAL_VRAM_RESERVE_MIB:-1536}"

info() { printf '[local-models] %s\n' "$*"; }
fail() { printf '[local-models] ERROR: %s\n' "$*" >&2; exit 1; }

require_file() {
    [ -f "$1" ] || fail "missing file: $1"
}

require_executable() {
    [ -x "$1" ] || fail "missing executable: $1"
}

pid_alive() {
    local name="$1" pid_file="$STATE_DIR/$1.pid" pid
    [ -r "$pid_file" ] || return 1
    pid="$(tr -cd '0-9' < "$pid_file")"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

wait_ready() {
    local name="$1" url="$2" attempts="${3:-120}"
    local count
    for ((count=1; count<=attempts; count++)); do
        if curl --silent --fail --max-time 2 \
            --header "Authorization: Bearer $MODEL_API_KEY" \
            "$url" >/dev/null 2>&1; then
            info "$name ready at $url"
            return 0
        fi
        sleep 1
    done
    tail -n 60 "$STATE_DIR/$name.log" >&2 || true
    fail "$name did not become ready"
}

preflight() {
    require_executable "$LLAMA_BIN_DIR/llama-server"
    require_executable "$COLI_DIR/coli"
    require_executable "$COLI_DIR/qwen36"
    require_executable "$NEEDLE_PYTHON"
    require_file "$LFM_MODEL"
    require_file "$KAT_DIR/config.json"
    require_file "$KAT_DIR/heat.bin"
    require_file "$NEEDLE_WEIGHTS"
    require_file "$NEEDLE_SIDECAR"
    command -v nvidia-smi >/dev/null || fail "nvidia-smi is unavailable"
    if [ "$MODEL_BIND_HOST" != "127.0.0.1" ] && [ "$MODEL_BIND_HOST" != "::1" ]; then
        [ "${#MODEL_API_KEY}" -ge 24 ] || fail "non-loopback binding requires ANANTA_LOCAL_MODEL_API_KEY with at least 24 characters"
    fi
    [ "${#NEEDLE_TOKEN}" -ge 24 ] || fail "ANANTA_NEEDLE_TOKEN (or model API key) must contain at least 24 characters"
    local free_mib required_mib
    free_mib="$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -n 1 | tr -d ' ')"
    required_mib=$((KAT_EXPERT_GB * 1024 + 3072 + VRAM_RESERVE_MIB))
    [ "$free_mib" -ge "$required_mib" ] || fail "VRAM preflight failed: free=${free_mib}MiB required=${required_mib}MiB"
    info "preflight passed: free=${free_mib}MiB required=${required_mib}MiB (including reserve)"
}

start_lfm() {
    pid_alive lfm && { info "LFM already running"; return; }
    mkdir -p "$STATE_DIR"
    nohup env LD_LIBRARY_PATH="$LLAMA_BIN_DIR:$CUDA_LIB_DIR:${LD_LIBRARY_PATH:-}" \
            LLAMA_API_KEY="$MODEL_API_KEY" \
            "$LLAMA_BIN_DIR/llama-server" \
            --model "$LFM_MODEL" --alias lfm2.5-2.6b-agentic-q8_0 \
            --host "$MODEL_BIND_HOST" --port "$LFM_PORT" --ctx-size "$LFM_CTX" \
            --parallel 1 --n-gpu-layers 99 --no-mmap \
            >"$STATE_DIR/lfm.log" 2>&1 &
    printf '%s\n' "$!" > "$STATE_DIR/lfm.pid"
    wait_ready lfm "http://$MODEL_BIND_HOST:$LFM_PORT/v1/models"
}

start_kat() {
    pid_alive kat && { info "KAT already running"; return; }
    mkdir -p "$STATE_DIR"
    nohup env COLI_MODEL="$KAT_DIR" COLI_GPUS=0 COLI_CUDA=1 \
            COLI_API_KEY="$MODEL_API_KEY" \
            CUDA_EXPERT_GB="$KAT_EXPERT_GB" CUDA_RESERVE_GB=2 \
            HEAT_FILE="$KAT_DIR/heat.bin" \
            OMP_NUM_THREADS="${ANANTA_KAT_THREADS:-12}" \
            "$COLI_DIR/coli" serve --host "$MODEL_BIND_HOST" --port "$KAT_PORT" \
            --model-id kat-coder-v2.5-dev --ctx "$KAT_CTX" --cap 256 \
            --allowed-host host.docker.internal \
            >"$STATE_DIR/kat.log" 2>&1 &
    printf '%s\n' "$!" > "$STATE_DIR/kat.pid"
    wait_ready kat "http://$MODEL_BIND_HOST:$KAT_PORT/v1/models" 180
}

start_needle() {
    pid_alive needle && { info "Needle already running"; return; }
    mkdir -p "$STATE_DIR"
    nohup env ANANTA_NEEDLE_BIND_HOST="$MODEL_BIND_HOST" \
            ANANTA_NEEDLE_PORT="$NEEDLE_PORT" \
            ANANTA_NEEDLE_TOKEN="$NEEDLE_TOKEN" \
            ANANTA_NEEDLE_WEIGHTS="$NEEDLE_WEIGHTS" \
            "$NEEDLE_PYTHON" "$NEEDLE_SIDECAR" \
            >"$STATE_DIR/needle.log" 2>&1 &
    printf '%s\n' "$!" > "$STATE_DIR/needle.pid"
    local saved_key="$MODEL_API_KEY"
    MODEL_API_KEY="$NEEDLE_TOKEN"
    wait_ready needle "http://$MODEL_BIND_HOST:$NEEDLE_PORT/ready"
    MODEL_API_KEY="$saved_key"
}

check_needle() {
    env NEEDLE_WEIGHTS="$NEEDLE_WEIGHTS" "$NEEDLE_PYTHON" - <<'PY'
import importlib.util, os
assert importlib.util.find_spec("needle") is not None, "needle_dependency_missing"
assert os.path.isfile(os.environ["NEEDLE_WEIGHTS"]), "needle_weights_missing"
print("needle ready (candidate-only CPU runtime)")
PY
}

stop_one() {
    local name="$1" pid_file="$STATE_DIR/$1.pid" pid
    [ -r "$pid_file" ] || return 0
    pid="$(tr -cd '0-9' < "$pid_file")"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid"
        local count
        for ((count=1; count<=30; count++)); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 1
        done
        kill -0 "$pid" 2>/dev/null && fail "$name did not stop after SIGTERM"
    fi
    rm -f "$pid_file"
}

status() {
    local name
    for name in lfm kat needle; do
        if pid_alive "$name"; then info "$name: running"; else info "$name: stopped"; fi
    done
    check_needle
    nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu --format=csv,noheader
}

supervise() {
    trap 'stop_one needle; stop_one kat; stop_one lfm' EXIT INT TERM
    preflight
    start_lfm
    start_kat
    start_needle
    status
    while pid_alive lfm && pid_alive kat && pid_alive needle; do
        sleep 10
    done
    fail "one or more local model processes exited"
}

case "${1:-status}" in
    preflight) preflight ;;
    start)
        preflight
        # Placement invariant: reserve the dense LFM allocation before KAT's expert tier.
        start_lfm
        start_kat
        start_needle
        status
        ;;
    stop)
        stop_one needle
        stop_one kat
        stop_one lfm
        status
        ;;
    restart) "$0" stop; "$0" start ;;
    supervise) supervise ;;
    status) status ;;
    *) fail "usage: $0 {preflight|start|stop|restart|supervise|status}" ;;
esac
