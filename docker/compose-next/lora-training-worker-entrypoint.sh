#!/bin/sh
set -eu

source_dir=${ANANTA_UNSLOTH_LLAMA_CPP_SOURCE:-}
runtime_dir=${UNSLOTH_LLAMA_CPP_PATH:-}

if [ -n "$source_dir" ]; then
    case "$runtime_dir" in
        /tmp/*) ;;
        *)
            echo "Unsloth llama.cpp runtime path must be contained in /tmp" >&2
            exit 78
            ;;
    esac
    if [ ! -x "$source_dir/llama-quantize" ] \
        || [ ! -f "$source_dir/convert_hf_to_gguf.py" ] \
        || [ ! -f "$source_dir/UNSLOTH_PREBUILT_INFO.json" ]; then
        echo "Pinned Unsloth llama.cpp source is incomplete" >&2
        exit 78
    fi

    mkdir -p "$(dirname "$runtime_dir")"
    if [ ! -d "$runtime_dir" ]; then
        staging_dir="${runtime_dir}.staging.$$"
        cp -a "$source_dir" "$staging_dir"
        mv "$staging_dir" "$runtime_dir"
    fi
    if [ -L "$runtime_dir" ] \
        || [ ! -x "$runtime_dir/llama-quantize" ] \
        || [ ! -f "$runtime_dir/convert_hf_to_gguf.py" ] \
        || ! cmp -s "$source_dir/UNSLOTH_PREBUILT_INFO.json" "$runtime_dir/UNSLOTH_PREBUILT_INFO.json"; then
        echo "Unsloth llama.cpp runtime copy failed integrity validation" >&2
        exit 78
    fi
fi

exec "$@"
