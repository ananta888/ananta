#!/bin/sh
set -eu

normalize_bool() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on)
      printf '1\n'
      ;;
    0|false|no|off|'')
      printf '0\n'
      ;;
    *)
      echo "model-bootstrap: invalid boolean value: $1" >&2
      return 2
      ;;
  esac
}

offline_mode="$(normalize_bool "${OLLAMA_BOOTSTRAP_OFFLINE:-0}")"

ensure_base_model() {
  model="$1"
  if ollama show "$model" >/dev/null 2>&1; then
    echo "model-bootstrap: base model already present: $model"
    return 0
  fi
  if [ "$offline_mode" = "1" ]; then
    echo "model-bootstrap: required base model missing in offline mode: $model" >&2
    return 1
  fi
  echo "model-bootstrap: pulling missing base model: $model"
  ollama pull "$model"
}

ensure_base_model phi4-mini
ensure_base_model gemma4:e4b-it-qat
ollama create ananta-phi4-mini-32k -f /modelfiles/ananta-phi4-mini-32k.Modelfile
ollama create ananta-gemma4-reasoning-8k -f /modelfiles/ananta-gemma4-reasoning-8k.Modelfile
