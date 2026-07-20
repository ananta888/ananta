#!/usr/bin/env python3
"""Measure the audio-free deterministic backend without persisting a report."""

from __future__ import annotations

import json
import resource
import tempfile
import time
from pathlib import Path

from worker.speech_training.backends.mock import fixture_digest


def main() -> None:
    fixture = Path("tests/fixtures/speech_training/mock_manifest.json")
    started = time.perf_counter_ns()
    with tempfile.TemporaryDirectory(prefix="ananta-speech-spike-") as temporary:
        root = Path(temporary)
        content = b"ANANTA-SPEECH-ADAPTER-V1\0" + bytes.fromhex(fixture_digest(fixture))
        artifact = root / "adapter.speech"
        artifact.write_bytes(content)
        disk_bytes = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        rss_kib = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        result = {
            "artifact_bytes": artifact.stat().st_size,
            "disk_bytes": disk_bytes,
            "elapsed_ms": round(elapsed_ms, 6),
            "fixture_digest": fixture_digest(fixture),
            "peak_rss_kib": rss_kib,
            "vram_bytes": 0,
            "backend": "mock",
            "quality_claim": False,
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
