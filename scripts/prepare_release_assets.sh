#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-release-assets}"
if [[ -z "$OUT_DIR" || "$OUT_DIR" == "/" || "$OUT_DIR" == "." || "$OUT_DIR" == ".." ]]; then
  echo "Refusing unsafe release asset directory: ${OUT_DIR}" >&2
  exit 2
fi
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/docs" "$OUT_DIR/compose" "$OUT_DIR/architecture"

copy_if_present() {
  local source="$1"
  local target="$2"
  if [[ -f "$source" ]]; then
    mkdir -p "$(dirname "$target")"
    cp "$source" "$target"
  fi
}

copy_if_present "release-verification-report.json" "$OUT_DIR/release-verification-report.json"
copy_if_present "release-sbom.json" "$OUT_DIR/release-sbom.json"
copy_if_present "README.md" "$OUT_DIR/README.md"
copy_if_present "LICENSE" "$OUT_DIR/LICENSE"

copy_if_present "docs/release-checklist.md" "$OUT_DIR/docs/release-checklist.md"
copy_if_present "docs/release-process.md" "$OUT_DIR/docs/release-process.md"
copy_if_present "docs/release-environment.md" "$OUT_DIR/docs/release-environment.md"
copy_if_present "docs/release-dependency-locking.md" "$OUT_DIR/docs/release-dependency-locking.md"
copy_if_present "docs/release-provenance.md" "$OUT_DIR/docs/release-provenance.md"
copy_if_present "docs/supply-chain-checks.md" "$OUT_DIR/docs/supply-chain-checks.md"
copy_if_present "docs/governance-security-model.md" "$OUT_DIR/docs/governance-security-model.md"
copy_if_present "docs/security_baseline.md" "$OUT_DIR/docs/security_baseline.md"

copy_if_present "docker-compose.base.yml" "$OUT_DIR/compose/docker-compose.base.yml"
copy_if_present "docker-compose-lite.yml" "$OUT_DIR/compose/docker-compose-lite.yml"
copy_if_present "docker-compose.yml" "$OUT_DIR/compose/docker-compose.yml"
copy_if_present "docker-compose.distributed.yml" "$OUT_DIR/compose/docker-compose.distributed.yml"
copy_if_present "docker-compose.ollama-wsl.yml" "$OUT_DIR/compose/docker-compose.ollama-wsl.yml"

if [[ -d "architektur/rendered" ]]; then
  find "architektur/rendered" -maxdepth 1 -type f -name "*.png" -exec cp {} "$OUT_DIR/architecture/" \;
fi

(
  cd "$OUT_DIR"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)
