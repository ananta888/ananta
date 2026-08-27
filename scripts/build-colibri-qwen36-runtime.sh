#!/usr/bin/env bash
set -euo pipefail

COLIBRI_ROOT="${ANANTA_COLIBRI_ROOT:-/home/krusty/moe-test/colibri}"
EXPECTED_REVISION="33e67a9c004b6e608d1f19dfbdcc20793377f94f"
PATCH_FILE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/patches/colibri-qwen36-request-hit-rate.patch"

[ -d "$COLIBRI_ROOT/.git" ] || { echo "colibri_source_repository_missing" >&2; exit 1; }
[ "$(git -C "$COLIBRI_ROOT" rev-parse HEAD)" = "$EXPECTED_REVISION" ] || {
    echo "colibri_source_revision_unverified" >&2
    exit 1
}
[ -z "$(git -C "$COLIBRI_ROOT" status --short --untracked-files=no)" ] || {
    echo "colibri_tracked_source_not_clean" >&2
    exit 1
}
if pgrep -f "^$COLIBRI_ROOT/c/qwen36( |$)" >/dev/null; then
    echo "colibri_qwen36_runtime_must_be_stopped" >&2
    exit 1
fi
git -C "$COLIBRI_ROOT" apply --check "$PATCH_FILE"
git -C "$COLIBRI_ROOT" apply "$PATCH_FILE"
restore_source() {
    git -C "$COLIBRI_ROOT" apply --reverse "$PATCH_FILE"
}
trap restore_source EXIT
make -C "$COLIBRI_ROOT/c" qwen36 \
    CUDA=1 \
    CUDA_HOME="$COLIBRI_ROOT/.cuda-toolkit" \
    CUDA_ARCH=sm_86
"$COLIBRI_ROOT/c/qwen36" --help >/dev/null 2>&1 || true
echo "colibri_qwen36_runtime_built_with_request_hit_rate"
