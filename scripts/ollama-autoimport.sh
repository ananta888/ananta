set -eu

mkdir -p /state/hash /state/logs /state/modelfiles

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://ollama:11434}"
OLLAMA_DEFAULT_ALIAS="${OLLAMA_DEFAULT_ALIAS:-ananta-default}"
OLLAMA_RESCAN_SEC="${OLLAMA_RESCAN_SEC:-30}"

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
  source_name="$1"
  is_text_model "$source_name" || return 0
  mf="/state/modelfiles/$OLLAMA_DEFAULT_ALIAS.Modelfile"
  printf 'FROM %s\n' "$source_name" > "$mf"
  create_model_from_file "$OLLAMA_DEFAULT_ALIAS" "$mf"
}

sanitize() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
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
    echo "$sb"
  elif [ "$sb" = "$s2" ]; then
    [ -n "$s1" ] && [ "$s1" != "." ] && echo "$s1-$s2" || echo "$s2"
  else
    [ -n "$s1" ] && [ "$s1" != "." ] && echo "$s1-$s2-$sb" || echo "$s2-$sb"
  fi
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
  hash_file="/state/hash/$(echo "${file#/models/}" | sed 's#[/ ]#_#g').sha256"
  old_hash=""
  [ -f "$hash_file" ] && old_hash="$(cat "$hash_file")"

  if [ "$hash_now" = "$old_hash" ]; then
    echo "unchanged: $name"
    return 0
  fi

  mf="/state/modelfiles/$name.Modelfile"
  printf 'FROM %s\n' "$file" > "$mf"

  echo "importing: $name from $file"
  if ! create_model_from_file "$name" "$mf"; then
    echo "failed: $name" >&2
    return 1
  fi
  ensure_default_alias_from_model "$name" || true

  printf '%s\n' "$hash_now" > "$hash_file"
  echo "done: $name"
}

scan_models() {
  find /models -type f \( -iname '*.gguf' -o -iname '*.GGUF' \) | while read -r f; do
    import_one "$f" || true
  done
}

echo "initial scan..."
scan_models

echo "rescanning /models every ${OLLAMA_RESCAN_SEC}s..."
while true; do
  sleep "$OLLAMA_RESCAN_SEC"
  scan_models
done
