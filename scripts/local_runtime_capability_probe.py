#!/usr/bin/env python3
"""Read-only, non-interactive diagnostics for a capability snapshot cache."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.services.local_runtime_capability_cache import LocalRuntimeCapabilityCache
from agent.services.local_runtime_capability_projection import LocalRuntimeCapabilityProjection


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--provider", choices=("ollama", "lmstudio"))
    args = parser.parse_args()
    payload = LocalRuntimeCapabilityProjection(LocalRuntimeCapabilityCache(args.cache)).snapshot()
    if args.provider:
        payload["snapshots"] = [item for item in payload["snapshots"] if item["provider_id"] == args.provider]
        payload["providers"] = [args.provider] if payload["snapshots"] else []
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False))
    if not payload["snapshots"]:
        return 2
    return 1 if payload["partial"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
