#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

shard_name="${1:-core-contracts-02}"
shard_count="${2:-14}"

tmp_matrix="$(mktemp)"
trap 'rm -f "$tmp_matrix"' EXIT

python scripts/resolve_backend_coverage_shards.py --shard-count "$shard_count" > "$tmp_matrix"

python - "$tmp_matrix" "$shard_name" <<'PY'
import json
import sys

matrix_path = sys.argv[1]
shard_name = sys.argv[2]

matrix = json.load(open(matrix_path, encoding="utf-8"))
shards = matrix.get("include") or []
target = next((shard for shard in shards if shard.get("shard_name") == shard_name), None)
if target is None:
    raise SystemExit(f"Shard {shard_name!r} not found in resolved backend coverage matrix")

files = target.get("files") or []
if not files:
    raise SystemExit(f"Shard {shard_name!r} has no test files")

print(f"[backend-coverage] shard={shard_name} file_count={target.get('file_count')} test_count={target.get('test_count')}")
print(f"[backend-coverage] files={len(files)}")
for file_name in files:
    print(file_name)
PY

mapfile -t shard_files < <(
  python - "$tmp_matrix" "$shard_name" <<'PY'
import json
import sys

matrix_path = sys.argv[1]
shard_name = sys.argv[2]
matrix = json.load(open(matrix_path, encoding="utf-8"))
shards = matrix.get("include") or []
target = next((shard for shard in shards if shard.get("shard_name") == shard_name), None)
if target is None:
    raise SystemExit(f"Shard {shard_name!r} not found in resolved backend coverage matrix")
for file_name in target.get("files") or []:
    print(file_name)
PY
)

if [[ "${#shard_files[@]}" -eq 0 ]]; then
  echo "No backend test files were assigned to shard ${shard_name}." >&2
  exit 1
fi

export COVERAGE_FILE=".coverage.${shard_name}"
pytest "${shard_files[@]}" --cov=agent --cov-report=
