set -eu

apk add --no-cache curl inotify-tools coreutils findutils grep sed

mkdir -p /state/hash /state/logs /state/modelfiles

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://ollama:11434}"
OLLAMA_DEFAULT_ALIAS="${OLLAMA_DEFAULT_ALIAS:-ananta-default}"

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

create_model() {
  name="$1"
  modelfile="$2"

  payload="$(printf '{"name":"%s","modelfile":"%s","stream":false}' \
    "$name" \
    "$(printf '%s' "$modelfile" | sed ':a;N;$!ba;s/\\/\\\\/g;s/"/\\"/g;s/\n/\\n/g')")"

  curl -fsS \
    -H 'Content-Type: application/json' \
    "$OLLAMA_BASE_URL/api/create" \
    -d "$payload" \
    >/dev/null
}

ensure_default_alias() {
  source_name="$1"
  is_text_model "$source_name" || return 0
  create_model "$OLLAMA_DEFAULT_ALIAS" "FROM $source_name"
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
  if ! create_model "$name" "FROM $file"; then
    echo "failed: $name" >&2
    return 1
  fi
  ensure_default_alias "$name" || true

  printf '%s\n' "$hash_now" > "$hash_file"
  echo "done: $name"
}

echo "initial scan..."
find /models -type f -iname '*.gguf' | while read -r f; do
  import_one "$f" || true
done

echo "watching /models..."
inotifywait -m -r -e create -e close_write -e moved_to /models --format '%w%f' \
  | while read -r changed; do
      case "$changed" in
        *.gguf|*.GGUF)
          import_one "$changed" || true
          ;;
      esac
    done
