set -eu

AUTOIMPORT_STATE_DIR="${AUTOIMPORT_STATE_DIR:-/state}"
OLLAMA_MODEL_NAME_MAX_LEN="${OLLAMA_MODEL_NAME_MAX_LEN:-80}"

mkdir -p "$AUTOIMPORT_STATE_DIR/hash" "$AUTOIMPORT_STATE_DIR/logs" "$AUTOIMPORT_STATE_DIR/modelfiles"

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://ollama:11434}"
OLLAMA_DEFAULT_ALIAS="${OLLAMA_DEFAULT_ALIAS:-ananta-default}"
OLLAMA_DEFAULT_ALIAS_CANDIDATES="${OLLAMA_DEFAULT_ALIAS_CANDIDATES:-bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s,lmstudio-community-qwen2.5-coder-14b-instruct-gguf-qwen2.5-coder-14-081c3c49a2d2,mradermacher-qwen2.5-coder-3b-instruct-distill-qwen3-coder-next-abl-0836a1d595c6}"
OLLAMA_SMOKE_ALIAS="${OLLAMA_SMOKE_ALIAS:-ananta-smoke}"
OLLAMA_SMOKE_ALIAS_CANDIDATES="${OLLAMA_SMOKE_ALIAS_CANDIDATES:-mradermacher-qwen2.5-coder-3b-instruct-distill-qwen3-coder-next-abl-0836a1d595c6,lmstudio-community-qwen2.5-coder-0.5b-instruct-gguf-qwen2.5-coder-0-8a0ee15fcff4,mradermacher-lfm2.5-1.2b-glm-4.7-flash-thinking-i1-gguf-lfm2.5-1.2b-c7d4a41ae661,bartowski-qwen2.5-coder-7b-instruct-gguf-qwen2.5-coder-7b-instruct-q4_k_s}"
OLLAMA_RESCAN_SEC="${OLLAMA_RESCAN_SEC:-30}"
OLLAMA_NUM_CTX="${OLLAMA_NUM_CTX:-32768}"
OLLAMA_AUTOIMPORT_MODE="${OLLAMA_AUTOIMPORT_MODE:-full}"
OLLAMA_READY_TIMEOUT_SEC="${OLLAMA_READY_TIMEOUT_SEC:-180}"
INITIAL_SCAN_PID=""

wait_for_ollama() {
  timeout_sec="$OLLAMA_READY_TIMEOUT_SEC"
  start_ts="$(date +%s)"
  while true; do
    if OLLAMA_HOST="$OLLAMA_BASE_URL" ollama list >/dev/null 2>&1; then
      return 0
    fi
    now_ts="$(date +%s)"
    elapsed="$((now_ts - start_ts))"
    if [ "$elapsed" -ge "$timeout_sec" ]; then
      echo "ollama-unavailable: timeout after ${timeout_sec}s at ${OLLAMA_BASE_URL}" >&2
      return 1
    fi
    sleep 2
  done
}

write_modelfile() {
  target="$1"
  source_ref="$2"
  printf 'FROM %s\n' "$source_ref" > "$target"
  if [ -n "$OLLAMA_NUM_CTX" ]; then
    printf 'PARAMETER num_ctx %s\n' "$OLLAMA_NUM_CTX" >> "$target"
  fi
}

is_text_model() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    *embed*|*embedding*|*rerank*|*whisper*|*tts*|*speech*|*audio*|*voxtral*|*mmproj*)
      return 1
      ;;
    *)
      return 0
      ;;
  esac
}

create_model_from_file() {
  name="$1"
  modelfile_path="$2"
  OLLAMA_HOST="$OLLAMA_BASE_URL" ollama create "$name" -f "$modelfile_path" >/dev/null
}

ensure_default_alias_from_model() {
  ensure_alias_from_model "$OLLAMA_DEFAULT_ALIAS" "$1"
}

normalize_model_ref() {
  printf '%s' "$1" | sed 's/:latest$//'
}

list_model_refs() {
  OLLAMA_HOST="$OLLAMA_BASE_URL" ollama list | awk 'NR>1 {print $1}'
}

model_exists() {
  wanted="$(normalize_model_ref "$1")"
  [ -n "$wanted" ] || return 1
  list_model_refs | sed 's/:latest$//' | grep -Fx -- "$wanted" >/dev/null 2>&1
}

ensure_alias_from_model() {
  alias_name="$1"
  source_name="$2"
  [ -n "$alias_name" ] || return 0
  [ -n "$source_name" ] || return 0
  source_name="$(normalize_model_ref "$source_name")"
  is_text_model "$source_name" || return 0
  mf="$AUTOIMPORT_STATE_DIR/modelfiles/$alias_name.Modelfile"
  write_modelfile "$mf" "$source_name"
  create_model_from_file "$alias_name" "$mf"
}

resolve_configured_alias_source() {
  candidates="$1"
  old_ifs="${IFS:- }"
  IFS=','
  set -- $candidates
  IFS="$old_ifs"
  for candidate in "$@"; do
    normalized="$(normalize_model_ref "$candidate")"
    [ -n "$normalized" ] || continue
    if model_exists "$normalized"; then
      printf '%s\n' "$normalized"
      return 0
    fi
  done
  return 1
}

priority_model_refs() {
  printf '%s\n%s\n' "$OLLAMA_DEFAULT_ALIAS_CANDIDATES" "$OLLAMA_SMOKE_ALIAS_CANDIDATES" \
    | tr ',' '\n' \
    | while IFS= read -r ref; do
        normalized="$(normalize_model_ref "$ref")"
        [ -n "$normalized" ] || continue
        printf '%s\n' "$normalized"
      done \
    | awk '!seen[$0]++'
}

resolve_import_seed_ref() {
  candidates="$1"
  old_ifs="${IFS:- }"
  IFS=','
  set -- $candidates
  IFS="$old_ifs"
  for candidate in "$@"; do
    normalized="$(normalize_model_ref "$candidate")"
    [ -n "$normalized" ] || continue
    if model_exists "$normalized"; then
      printf '%s\n' "$normalized"
      return 0
    fi
    if find_model_file_for_ref "$normalized" >/dev/null 2>&1; then
      printf '%s\n' "$normalized"
      return 0
    fi
  done
  return 1
}

seed_model_refs() {
  {
    resolve_import_seed_ref "$OLLAMA_DEFAULT_ALIAS_CANDIDATES" || true
    resolve_import_seed_ref "$OLLAMA_SMOKE_ALIAS_CANDIDATES" || true
  } | awk 'NF && !seen[$0]++'
}

first_available_text_model() {
  list_model_refs | while IFS= read -r ref; do
    normalized="$(normalize_model_ref "$ref")"
    is_text_model "$normalized" || continue
    printf '%s\n' "$normalized"
    break
  done
}

ensure_configured_alias() {
  alias_name="$1"
  configured_candidates="$2"
  source_name="$(resolve_configured_alias_source "$configured_candidates" || true)"
  if [ -z "$source_name" ]; then
    source_name="$(first_available_text_model || true)"
  fi
  [ -n "$source_name" ] || return 0
  ensure_alias_from_model "$alias_name" "$source_name"
}

ensure_configured_aliases() {
  ensure_configured_alias "$OLLAMA_DEFAULT_ALIAS" "$OLLAMA_DEFAULT_ALIAS_CANDIDATES"
  ensure_configured_alias "$OLLAMA_SMOKE_ALIAS" "$OLLAMA_SMOKE_ALIAS_CANDIDATES"
}

sanitize() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
}

trim_model_name() {
  name="$1"
  max_len="$OLLAMA_MODEL_NAME_MAX_LEN"
  hash_suffix="$(printf '%s' "$name" | sha256sum | awk '{print substr($1, 1, 12)}')"

  if [ "${#name}" -le "$max_len" ]; then
    printf '%s\n' "$name"
    return 0
  fi

  prefix_len=$((max_len - ${#hash_suffix} - 1))
  if [ "$prefix_len" -lt 1 ]; then
    prefix_len=1
  fi

  trimmed="$(printf '%s' "$name" | cut -c1-"$prefix_len" | sed -E 's/[-._]+$//')"
  [ -n "$trimmed" ] || trimmed="m"
  printf '%s-%s\n' "$trimmed" "$hash_suffix"
}

model_name() {
  file="$1"
  rel="${file#/models/}"
  dir="$(dirname "$rel")"
  base="$(basename "$file" .gguf)"
  p1="$(basename "$(dirname "$dir")")"
  p2="$(basename "$dir")"
  s1="$(sanitize "$p1")"
  s2="$(sanitize "$p2")"
  sb="$(sanitize "$base")"

  if [ "$s2" = "." ] || [ -z "$s2" ]; then
    name="$sb"
  elif [ "$sb" = "$s2" ]; then
    if [ -n "$s1" ] && [ "$s1" != "." ]; then
      name="$s1-$s2"
    else
      name="$s2"
    fi
  else
    if [ -n "$s1" ] && [ "$s1" != "." ]; then
      name="$s1-$s2-$sb"
    else
      name="$s2-$sb"
    fi
  fi

  trim_model_name "$name"
}

import_one() {
  file="$1"
  [ -f "$file" ] || return 0

  case "$file" in
    *.gguf|*.GGUF) ;;
    *) return 0 ;;
  esac

  name="$(model_name "$file")"
  hash_now="$(sha256sum "$file" | awk '{print $1}')"
  hash_file="$AUTOIMPORT_STATE_DIR/hash/$(echo "${file#/models/}" | sed 's#[/ ]#_#g').sha256"
  old_hash=""
  [ -f "$hash_file" ] && old_hash="$(cat "$hash_file")"

  if [ "$hash_now" = "$old_hash" ] && model_exists "$name"; then
    echo "unchanged: $name"
    return 0
  fi

  mf="$AUTOIMPORT_STATE_DIR/modelfiles/$name.Modelfile"
  write_modelfile "$mf" "$file"

  echo "importing: $name from $file"
  if ! create_model_from_file "$name" "$mf"; then
    echo "failed: $name" >&2
    return 1
  fi

  printf '%s\n' "$hash_now" > "$hash_file"
  echo "done: $name"
}

find_model_file_for_ref() {
  wanted="$(normalize_model_ref "$1")"
  [ -n "$wanted" ] || return 1
  find /models -type f \( -iname '*.gguf' -o -iname '*.GGUF' \) | while IFS= read -r f; do
    candidate_name="$(model_name "$f")"
    if [ "$candidate_name" = "$wanted" ]; then
      printf '%s\n' "$f"
      return 0
    fi
  done
  return 1
}

import_priority_models() {
  priority_model_refs | while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    if model_exists "$ref"; then
      echo "priority-ready: $ref"
      continue
    fi
    file_path="$(find_model_file_for_ref "$ref" || true)"
    if [ -z "$file_path" ]; then
      echo "priority-missing: $ref"
      continue
    fi
    import_one "$file_path" || true
  done
}

import_seed_models() {
  seed_model_refs | while IFS= read -r ref; do
    [ -n "$ref" ] || continue
    if model_exists "$ref"; then
      echo "seed-ready: $ref"
      continue
    fi
    file_path="$(find_model_file_for_ref "$ref" || true)"
    if [ -z "$file_path" ]; then
      echo "seed-missing: $ref"
      continue
    fi
    import_one "$file_path" || true
  done
}

scan_models() {
  find /models -type f \( -iname '*.gguf' -o -iname '*.GGUF' \) | while read -r f; do
    import_one "$f" || true
  done
}

start_bulk_scan() {
  log_file="$AUTOIMPORT_STATE_DIR/logs/bulk-import.log"
  (
    echo "bulk-import-start: $(date -Iseconds)"
    scan_models
    echo "bulk-import-finish: $(date -Iseconds)"
  ) >>"$log_file" 2>&1 &
  INITIAL_SCAN_PID="$!"
  echo "bulk-import-pid: $INITIAL_SCAN_PID"
}

wait_for_bulk_scan_if_running() {
  if [ -n "${INITIAL_SCAN_PID:-}" ] && kill -0 "$INITIAL_SCAN_PID" >/dev/null 2>&1; then
    wait "$INITIAL_SCAN_PID" || true
  fi
  INITIAL_SCAN_PID=""
}

autoimport_mode_is_seed_only() {
  case "$(printf '%s' "$OLLAMA_AUTOIMPORT_MODE" | tr '[:upper:]' '[:lower:]')" in
    seed|seed-only|seeds|minimal)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

main() {
  wait_for_ollama
  echo "seed import..."
  import_seed_models
  ensure_configured_aliases

  if autoimport_mode_is_seed_only; then
    echo "autoimport mode: seed-only"
    return 0
  fi

  echo "autoimport mode: full"
  start_bulk_scan

  echo "rescanning /models every ${OLLAMA_RESCAN_SEC}s..."
  while true; do
    sleep "$OLLAMA_RESCAN_SEC"
    wait_for_bulk_scan_if_running
    scan_models
    ensure_configured_aliases
  done
}

if [ "${OLLAMA_AUTOIMPORT_LIB_ONLY:-0}" != "1" ]; then
  main
fi
