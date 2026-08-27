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
COLIBRI_GATEWAY="${ANANTA_COLIBRI_GATEWAY:-/home/krusty/ananta/scripts/colibri-ananta-gateway.py}"
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
KAT_RAM_GB="${ANANTA_KAT_RAM_GB:-40}"
VRAM_RESERVE_MIB="${ANANTA_LOCAL_VRAM_RESERVE_MIB:-1536}"

info() { printf '[local-models] %s\n' "$*"; }
fail() { printf '[local-models] ERROR: %s\n' "$*" >&2; exit 1; }

load_effective_contexts() {
    local context_file="$STATE_DIR/active-contexts.env"
    [ -r "$context_file" ] || return 0
    local key value seen_kat=0 seen_lfm=0 seen_needle=0 seen_digest=0
    while IFS='=' read -r key value; do
        case "$key" in
            ANANTA_KAT_CTX)
                [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge 1 ] && [ "$value" -le 100000000 ] || fail "invalid active KAT context"
                KAT_CTX="$value"; seen_kat=1 ;;
            ANANTA_LFM_CTX)
                [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge 1 ] && [ "$value" -le 100000000 ] || fail "invalid active LFM context"
                LFM_CTX="$value"; seen_lfm=1 ;;
            ANANTA_NEEDLE_CTX)
                [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge 1 ] && [ "$value" -le 256 ] || fail "invalid active Needle context"
                seen_needle=1 ;;
            ANANTA_LOCAL_RUNTIME_DECISION_DIGEST)
                [[ "$value" =~ ^[a-f0-9]{64}$ ]] || fail "invalid active runtime decision digest"
                seen_digest=1 ;;
            *) fail "unknown active runtime context field" ;;
        esac
    done < "$context_file"
    [ "$seen_kat$seen_lfm$seen_needle$seen_digest" = "1111" ] || fail "incomplete active runtime contexts"
}

load_effective_contexts

require_file() {
    [ -f "$1" ] || fail "missing file: $1"
}

require_executable() {
    [ -x "$1" ] || fail "missing executable: $1"
}

pid_alive() {
    local name="$1" pid_file="$STATE_DIR/$1.pid" pid
    [ -r "$pid_file" ] || return 1
    pid="$(tr -d '\n' < "$pid_file")"
    [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -ge 1 ] && kill -0 "$pid" 2>/dev/null && pid_matches "$name" "$pid"
}

pid_matches() {
    local name="$1" pid="$2" expected
    case "$name" in
        lfm) expected="$LLAMA_BIN_DIR/llama-server" ;;
        kat) expected="$COLIBRI_GATEWAY" ;;
        needle) expected="$NEEDLE_SIDECAR" ;;
        *) return 1 ;;
    esac
    [ -r "/proc/$pid/cmdline" ] && tr '\0' '\n' < "/proc/$pid/cmdline" | grep -Fqx -- "$expected"
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
    require_file "$COLIBRI_GATEWAY"
    require_executable "$NEEDLE_PYTHON"
    require_file "$LFM_MODEL"
    require_file "$KAT_DIR/config.json"
    require_file "$KAT_DIR/heat.bin"
    require_file "$NEEDLE_WEIGHTS"
    require_file "$NEEDLE_SIDECAR"
    [[ "$KAT_RAM_GB" =~ ^[0-9]+$ ]] && [ "$KAT_RAM_GB" -ge 1 ] && [ "$KAT_RAM_GB" -le 48 ] || fail "KAT RAM budget must be 1..48 GiB"
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
            RAM_GB="$KAT_RAM_GB" \
            Q36_MAXT="$KAT_CTX" \
            HEAT_FILE="$KAT_DIR/heat.bin" \
            OMP_NUM_THREADS="${ANANTA_KAT_THREADS:-12}" \
            ANANTA_COLIBRI_DIR="$COLI_DIR" \
            python3 "$COLIBRI_GATEWAY" --model "$KAT_DIR" --engine "$COLI_DIR/qwen36" \
            --host "$MODEL_BIND_HOST" --port "$KAT_PORT" \
            --model-id kat-coder-v2.5-dev --max-tokens 256 --cap 256 \
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
    pid="$(tr -d '\n' < "$pid_file")"
    if pid_alive "$name"; then
        kill -TERM "$pid" 2>/dev/null || true
        local count
        for ((count=1; count<=30; count++)); do
            pid_alive "$name" || break
            sleep 1
        done
        pid_alive "$name" && fail "$name did not stop after SIGTERM"
    else
        info "$name: stale or foreign PID file removed without signalling a process"
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
    shutdown_supervisor() {
        # systemd signals the complete control group.  Treat that requested
        # shutdown as success and prevent the EXIT trap from running twice.
        trap - EXIT INT TERM
        stop_one needle
        stop_one kat
        stop_one lfm
        exit 0
    }
    trap shutdown_supervisor INT TERM
    trap 'stop_one needle; stop_one kat; stop_one lfm' EXIT
    preflight
    start_lfm
    start_kat
    start_needle
    status
    if [ -n "${NOTIFY_SOCKET:-}" ]; then
        systemd-notify --ready --status="KAT, LFM and Needle are ready"
    fi
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
