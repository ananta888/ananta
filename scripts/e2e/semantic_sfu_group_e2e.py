#!/usr/bin/env python3
"""Run the pinned local SFU with real Chromium/Firefox fan-out and load probes.

The command is deliberately opt-in and writes evidence only after every live
step succeeds.  It owns the temporary Compose deployment, but it never calls
Hub task APIs and never persists media payloads.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.semantic-media.yml"
SPIKE = ROOT / "scripts/spikes/semantic_sfu_three_peer.mjs"
EXPECTED_IMAGE_ID = "2c6869d2d5ff6c9c0166f47be1c92dad6928bfecfa5e4060a6ece48db8accfa3"


class LiveSfuError(RuntimeError):
    pass


def percentile(values: list[int], percentage: float) -> int:
    if not values:
        raise LiveSfuError("sfu_load_samples_missing")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentage) - 1))
    return ordered[index]


def run_command(command: list[str], *, env: Mapping[str, str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, cwd=ROOT, env=dict(env), check=False, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise LiveSfuError(f"live_sfu_command_failed:{Path(command[0]).name}:{result.returncode}")
    return result


def inspect_image() -> None:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", "livekit/livekit-server:v1.13.1"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    digest = result.stdout.strip().removeprefix("sha256:")
    if result.returncode != 0 or digest != EXPECTED_IMAGE_ID:
        raise LiveSfuError("live_sfu_image_digest_mismatch")


def container_stats(container_id: str, env: Mapping[str, str]) -> tuple[float, int]:
    output = run_command(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", container_id], env=env, timeout=30
    ).stdout.strip()
    row = json.loads(output)
    cpu = float(str(row["CPUPerc"]).removesuffix("%"))
    used = str(row["MemUsage"]).split("/", 1)[0].strip()
    return cpu, parse_size(used)


def parse_size(value: str) -> int:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)", value)
    if not match:
        raise LiveSfuError("live_sfu_memory_stat_invalid")
    unit = match.group(2)
    factors = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3,
               "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    return int(float(match.group(1)) * factors[unit])


def run_spike(
    *,
    env: Mapping[str, str],
    output: Path,
    engines: str,
    receiver_count: int,
    wrong_key: bool,
) -> tuple[dict[str, Any], int]:
    child = dict(env)
    child.update(
        {
            "ANANTA_SEMANTIC_MEDIA_SFU_BROWSER_ENGINES": engines,
            "ANANTA_SEMANTIC_MEDIA_SFU_RECEIVER_COUNT": str(receiver_count),
            "ANANTA_SEMANTIC_MEDIA_SFU_WRONG_KEY_PROBE": "1" if wrong_key else "0",
            "ANANTA_SEMANTIC_MEDIA_SFU_SPIKE_OUTPUT": output.relative_to(ROOT).as_posix()
            if output.is_relative_to(ROOT) else str(output),
        }
    )
    started = time.monotonic_ns()
    run_command(["node", str(SPIKE.relative_to(ROOT))], env=child, timeout=240)
    elapsed_ms = math.ceil((time.monotonic_ns() - started) / 1_000_000)
    report = json.loads(output.read_text(encoding="utf-8"))
    if report.get("verdict") != "pass":
        raise LiveSfuError("live_sfu_spike_failed")
    return report, elapsed_ms


def load_report(samples: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    thresholds = {
        "max_cpu_percent": 80.0,
        "max_memory_bytes": 536_870_912,
        "max_completion_p99_ms": 30_000,
        "max_dropped_video_frames": 8,
    }
    levels: list[dict[str, Any]] = []
    for participants, rows in sorted(samples.items()):
        ingress = [row["ingress"] for row in rows]
        egress = [row["egress"] for row in rows]
        cpus = [row["cpu"] for row in rows]
        memory = [row["memory"] for row in rows]
        latency = [row["latency"] for row in rows]
        drops = [row["drops"] for row in rows]
        level = {
            "participants": participants,
            "samples": len(rows),
            "publisher_ingress_bytes": {"min": min(ingress), "max": max(ingress)},
            "receiver_egress_bytes": {"min": min(egress), "max": max(egress)},
            "cpu_percent": {"max": max(cpus), "mean": round(statistics.fmean(cpus), 3)},
            "memory_bytes": {"max": max(memory)},
            "turn_relay_selected_pairs": max(row["turn"] for row in rows),
            "dropped_video_frames": {"max": max(drops)},
            "scenario_completion_latency_ms": {
                "p95": percentile(latency, 0.95),
                "p99": percentile(latency, 0.99),
            },
        }
        level["verdict"] = "pass" if (
            min(ingress) > 0
            and min(egress) > 0
            and max(cpus) <= thresholds["max_cpu_percent"]
            and max(memory) <= thresholds["max_memory_bytes"]
            and max(drops) <= thresholds["max_dropped_video_frames"]
            and level["scenario_completion_latency_ms"]["p99"] <= thresholds["max_completion_p99_ms"]
        ) else "fail"
        levels.append(level)
    return {
        "schema": "ananta.semantic-sfu-load.v1",
        "scope": "local-loopback; not a broadcast/production capacity claim",
        "pinned_server_digest": f"sha256:{EXPECTED_IMAGE_ID}",
        "thresholds": thresholds,
        "levels": levels,
        "verdict": "pass" if all(level["verdict"] == "pass" for level in levels) else "fail",
    }


def sample_metrics(report: Mapping[str, Any], latency: int, cpu: float, memory: int) -> dict[str, Any]:
    engine = report["engines"][0]
    peers = engine["peers"]
    publisher = next(row for row in peers if row["identity"] == "publisher")
    receivers = [row for row in peers if str(row["identity"]).startswith("receiver-")]
    return {
        "ingress": int(publisher["outbound_video_bytes"]),
        "egress": sum(int(row["inbound_video_bytes"]) for row in receivers),
        "drops": sum(int(row.get("dropped_video_frames", 0)) for row in receivers),
        "turn": sum(
            any(str(candidate).startswith("relay:") for candidate in row.get("selected_candidate_types", []))
            for row in peers
        ),
        "latency": latency,
        "cpu": cpu,
        "memory": memory,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spike-output", type=Path, default=ROOT / "artifacts/domain/semantic-sfu-three-peer.json")
    parser.add_argument("--load-output", type=Path, default=ROOT / "artifacts/domain/semantic-sfu-load.json")
    parser.add_argument("--samples", type=int, default=3)
    args = parser.parse_args()
    if not 3 <= args.samples <= 10:
        raise SystemExit("--samples must be between 3 and 10")
    inspect_image()
    env = dict(os.environ)
    env.update(
        {
            "ANANTA_SEMANTIC_MEDIA_SFU_API_KEY": "ananta-local-spike",
            "ANANTA_SEMANTIC_MEDIA_SFU_API_SECRET": secrets.token_urlsafe(48),
            "ANANTA_SEMANTIC_MEDIA_SFU_PUBLIC_WS_URL": "ws://127.0.0.1:7880",
        }
    )
    compose = [
        "docker", "compose", "--project-name", f"ananta-semantic-sfu-{secrets.token_hex(6)}",
        "-f", str(COMPOSE), "--profile", "semantic-media-sfu",
    ]
    try:
        run_command([*compose, "up", "-d", "--wait", "semantic-media-sfu"], env=env, timeout=120)
        container_id = run_command([*compose, "ps", "-q", "semantic-media-sfu"], env=env, timeout=30).stdout.strip()
        if not re.fullmatch(r"[a-f0-9]{12,64}", container_id):
            raise LiveSfuError("live_sfu_container_missing")
        with tempfile.TemporaryDirectory(prefix="ananta-sfu-e2e-") as temporary:
            root = Path(temporary)
            functional, _ = run_spike(
                env=env, output=root / "functional.json", engines="chromium,firefox",
                receiver_count=2, wrong_key=True,
            )
            samples: dict[int, list[dict[str, Any]]] = {2: [], 4: []}
            for participants in samples:
                for index in range(args.samples):
                    report, latency = run_spike(
                        env=env, output=root / f"load-{participants}-{index}.json", engines="chromium",
                        receiver_count=participants - 1, wrong_key=False,
                    )
                    cpu, memory = container_stats(container_id, env)
                    samples[participants].append(sample_metrics(report, latency, cpu, memory))
            load = load_report(samples)
            args.spike_output.parent.mkdir(parents=True, exist_ok=True)
            args.load_output.parent.mkdir(parents=True, exist_ok=True)
            args.spike_output.write_text(json.dumps(functional, indent=2) + "\n", encoding="utf-8")
            args.load_output.write_text(json.dumps(load, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, LiveSfuError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "reason_code": str(exc).split(":", 1)[0]}, sort_keys=True))
        return 2
    finally:
        subprocess.run([*compose, "down", "--remove-orphans"], cwd=ROOT, env=env, check=False, timeout=120)
    print(json.dumps({"ok": True, "samples": args.samples, "engines": ["chromium", "firefox"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
