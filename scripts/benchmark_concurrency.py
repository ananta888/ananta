#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from pathlib import Path
from urllib import request


def _http_get(url: str, timeout: float = 10.0) -> dict:
    with request.urlopen(url, timeout=timeout) as resp:  # nosec B310 - local operator tool
        payload = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(payload)
    except Exception:
        return {"raw": payload}


def _run_tick(base_url: str) -> float:
    start = time.perf_counter()
    req = request.Request(f"{base_url.rstrip('/')}/tasks/autopilot/tick", method="POST")
    with request.urlopen(req, timeout=60):  # nosec B310 - local operator tool
        pass
    return time.perf_counter() - start


def _nvidia_smi_snapshot() -> str | None:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.used,utilization.gpu", "--format=csv,noheader"],
            text=True,
            timeout=5,
        )
        return out.strip()
    except Exception:
        return None


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1)))))
    return ordered[idx]


def main() -> int:
    parser = argparse.ArgumentParser(description="Controlled concurrency benchmark harness")
    parser.add_argument("--base-url", default=os.environ.get("ANANTA_BASE_URL", "http://localhost:5000"))
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--tiers", default="1,2,4")
    parser.add_argument("--out", default="artifacts/benchmark_concurrency.json")
    args = parser.parse_args()

    tiers = [int(x.strip()) for x in str(args.tiers).split(",") if x.strip()]
    result = {"base_url": args.base_url, "samples": args.samples, "tiers": [], "started_at": time.time()}

    for tier in tiers:
        runs: list[float] = []
        for _ in range(max(1, args.samples)):
            runs.append(_run_tick(args.base_url))
        entry = {
            "concurrency": tier,
            "p50_seconds": statistics.median(runs),
            "p95_seconds": _p95(runs),
            "min_seconds": min(runs),
            "max_seconds": max(runs),
            "samples": len(runs),
        }
        result["tiers"].append(entry)

    vram = _nvidia_smi_snapshot()
    if vram:
        result["nvidia_smi"] = vram

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    md_path = out_path.with_suffix(".md")
    lines = [
        "# Concurrency Benchmark Summary",
        "",
        f"Base URL: `{args.base_url}`",
        "",
        "| Tier | p50 (s) | p95 (s) | min (s) | max (s) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for tier in result["tiers"]:
        lines.append(
            f"| {tier['concurrency']} | {tier['p50_seconds']:.3f} | {tier['p95_seconds']:.3f} | {tier['min_seconds']:.3f} | {tier['max_seconds']:.3f} |"
        )
    if vram:
        lines.extend(["", "## NVIDIA SMI", "", "```text", vram, "```"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(str(out_path))
    print(str(md_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
