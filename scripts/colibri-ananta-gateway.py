#!/usr/bin/env python3
"""Run Colibri's gateway with additive, request-scoped runtime telemetry."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping


def extended_usage(stats: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve OpenAI usage fields and expose measured engine statistics."""

    prompt = int(stats["prompt_tokens"])
    completion = int(stats["completion_tokens"])
    hit_percent = float(stats["cache_hit_percent"])
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "x_ananta_runtime": {
            "expert_cache_hit_rate": max(0.0, min(hit_percent / 100.0, 1.0)),
            "engine_tokens_per_second": max(0.0, float(stats["tokens_per_second"])),
        },
    }


def main() -> None:
    colibri_dir = Path(os.environ.get("ANANTA_COLIBRI_DIR") or "").resolve()
    if not colibri_dir.is_dir() or not (colibri_dir / "openai_server.py").is_file():
        raise SystemExit("ananta_colibri_gateway_directory_invalid")
    sys.path.insert(0, str(colibri_dir))
    import openai_server  # type: ignore[import-not-found]  # noqa: PLC0415

    # Colibri intentionally keeps its OpenAI usage shape minimal.  The Ananta
    # operator adapter adds namespaced, content-free engine facts without
    # changing engine code or orchestration ownership.
    openai_server.APIHandler.usage = staticmethod(extended_usage)
    openai_server.main()


if __name__ == "__main__":
    main()
