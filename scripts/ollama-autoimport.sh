set -eu

apk add --no-cache curl inotify-tools coreutils findutils grep sed

mkdir -p /state/hash /state/logs /state/modelfiles

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
  curl -fsS http://ollama:11434/api/create \
    -d "$(printf '{"name":"%s","path":"%s"}' "$name" "$mf")"

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
