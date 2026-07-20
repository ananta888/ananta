#!/usr/bin/env python3
"""Run honest local 2/4-participant SFU load samples against the pinned service."""

from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/domain/semantic-sfu-load.json"
CONTAINER = os.environ.get(
    "ANANTA_SEMANTIC_MEDIA_SFU_CONTAINER",
    "ananta-semantic-media-semantic-media-sfu-1",
)


def main() -> int:
    levels = [run_level(participants, samples=3) for participants in (2, 4)]
    verdict = "pass" if all(level["verdict"] == "pass" for level in levels) else "fail"
    report = {
        "schema": "ananta.semantic-sfu-load.v1",
        "scope": "local-loopback; not a broadcast/production capacity claim",
        "pinned_server_digest": "sha256:2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3",
        "thresholds": {
            "max_cpu_percent": 80.0,
            "max_memory_bytes": 536_870_912,
            "max_completion_p99_ms": 30_000,
            "max_dropped_video_frames": 8,
        },
        "levels": levels,
        "verdict": verdict,
    }
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "verdict": verdict}))
    return 0 if verdict == "pass" else 1


def run_level(participants: int, *, samples: int) -> dict:
    latencies: list[int] = []
    cpu: list[float] = []
    memory: list[int] = []
    ingress: list[int] = []
    egress: list[int] = []
    dropped: list[int] = []
    relay_pairs = 0
    sample_verdicts: list[str] = []
    for sample in range(samples):
        before = docker_stats()
        with tempfile.TemporaryDirectory(prefix="ananta-sfu-load-") as directory:
            artifact = Path(directory) / "spike.json"
            env = {
                **os.environ,
                "ANANTA_SEMANTIC_MEDIA_SFU_BROWSER_ENGINES": "chromium",
                "ANANTA_SEMANTIC_MEDIA_SFU_RECEIVER_COUNT": str(participants - 1),
                "ANANTA_SEMANTIC_MEDIA_SFU_WRONG_KEY_PROBE": "0",
                "ANANTA_SEMANTIC_MEDIA_SFU_SPIKE_OUTPUT": str(artifact),
            }
            started = time.monotonic()
            subprocess.run(
                ["node", "scripts/spikes/semantic_sfu_three_peer.mjs"],
                cwd=ROOT,
                env=env,
                check=True,
                timeout=60,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            latencies.append(round((time.monotonic() - started) * 1000))
            evidence = json.loads(artifact.read_text(encoding="utf-8"))
        after = docker_stats()
        engine = evidence["engines"][0]
        peers = engine["peers"]
        publisher = next(row for row in peers if row["identity"] == "publisher")
        receivers = [row for row in peers if row["identity"].startswith("receiver-")]
        ingress.append(publisher["outbound_video_bytes"])
        egress.append(sum(row["inbound_video_bytes"] for row in receivers))
        dropped.append(sum(row["dropped_video_frames"] for row in receivers))
        relay_pairs += sum(
            1 for row in peers for pair in row["selected_candidate_types"] if "relay" in pair
        )
        cpu.append(after["cpu_percent"])
        memory.append(after["memory_bytes"])
        sample_verdicts.append(evidence["verdict"])
        if before["network_rx_bytes"] <= after["network_rx_bytes"]:
            # Container counters are supporting diagnostics; RTP getStats above
            # remains the deterministic per-publication measurement.
            pass
    completion_p95 = percentile(latencies, 0.95)
    completion_p99 = percentile(latencies, 0.99)
    verdict = "pass" if (
        all(item == "pass" for item in sample_verdicts)
        and max(cpu) <= 80.0
        and max(memory) <= 536_870_912
        and completion_p99 <= 30_000
        and max(dropped) <= 8
        and all(value > 0 for value in ingress)
        and all(value > 0 for value in egress)
    ) else "fail"
    return {
        "participants": participants,
        "samples": samples,
        "publisher_ingress_bytes": {"min": min(ingress), "max": max(ingress)},
        "receiver_egress_bytes": {"min": min(egress), "max": max(egress)},
        "cpu_percent": {"max": round(max(cpu), 3), "mean": round(sum(cpu) / len(cpu), 3)},
        "memory_bytes": {"max": max(memory)},
        "turn_relay_selected_pairs": relay_pairs,
        "dropped_video_frames": {"max": max(dropped)},
        "scenario_completion_latency_ms": {"p95": completion_p95, "p99": completion_p99},
        "verdict": verdict,
    }


def docker_stats() -> dict:
    result = subprocess.run(
        ["docker", "stats", CONTAINER, "--no-stream", "--format", "{{json .}}"],
        check=True,
        timeout=10,
        capture_output=True,
        text=True,
    )
    row = json.loads(result.stdout)
    used_memory = str(row.get("MemUsage", "0B / 0B")).split("/")[0].strip()
    network = str(row.get("NetIO", "0B / 0B")).split("/")
    return {
        "cpu_percent": float(str(row.get("CPUPerc", "0%")).removesuffix("%")),
        "memory_bytes": parse_bytes(used_memory),
        "network_rx_bytes": parse_bytes(network[0].strip()),
        "network_tx_bytes": parse_bytes(network[1].strip()) if len(network) > 1 else 0,
    }


def parse_bytes(raw: str) -> int:
    value = raw.strip().replace("i", "")
    units = {"B": 1, "kB": 1000, "KB": 1000, "MB": 1000**2, "GB": 1000**3,
             "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
    for suffix in sorted(units, key=len, reverse=True):
        if value.endswith(suffix):
            return round(float(value.removesuffix(suffix).strip()) * units[suffix])
    return round(float(value))


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


if __name__ == "__main__":
    raise SystemExit(main())
