#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${1:-$ROOT_DIR/deploy/examples/llm-interceptor.config.example.json}"

echo "[smoke] validating interceptor config: $CONFIG_PATH"
python -m agent.cli.main runtime llm-interceptor --config "$CONFIG_PATH" --dry-run
echo "[smoke] ok"

