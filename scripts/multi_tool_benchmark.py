#!/usr/bin/env python3
"""Bounded direct/native-function-calling/hybrid benchmark on one local runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.services.decision_providers.local_http import NoRedirect, default_local_hosts, validate_local_endpoint
from agent.services.multi_tool_benchmark import run_comparison


class LocalBenchmarkTransport:
    def __init__(self, url: str):
        validate_local_endpoint(url, default_local_hosts(url))
        self._url = url.rstrip("/")
        self._opener = urllib.request.build_opener(NoRedirect()).open

    def __call__(self, path, body, deadline):
        started = time.monotonic()
        request = urllib.request.Request(
            self._url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        with self._opener(request, timeout=max(0.05, deadline - started)) as response:
            if not body.get("stream"):
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("benchmark_response_too_large")
                payload = json.loads(raw)
                return payload, {
                    "ttft_ms": None,
                    "response_ms": (time.monotonic() - started) * 1000,
                    "timings": payload.get("timings"),
                    "usage": payload.get("usage"),
                }
            calls, content, first, size, model, finish = {}, "", None, 0, None, None
            usage = None
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("benchmark_deadline")
                line = response.readline(65537)
                size += len(line)
                if size > 2 * 1024 * 1024 or len(line) > 65536:
                    raise ValueError("benchmark_stream_too_large")
                if not line:
                    raise ValueError("benchmark_stream_incomplete")
                if not line.startswith(b"data: "):
                    continue
                raw = line[6:].strip()
                if raw == b"[DONE]":
                    break
                chunk = json.loads(raw)
                model = chunk.get("model", model)
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices", []):
                    finish = choice.get("finish_reason") or finish
                    delta = choice.get("delta") or {}
                    if first is None and (delta.get("content") or delta.get("tool_calls")):
                        first = (time.monotonic() - started) * 1000
                    content += delta.get("content") or ""
                    for call in delta.get("tool_calls") or []:
                        entry = calls.setdefault(
                            call["index"], {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                        )
                        entry["id"] += call.get("id") or ""
                        for key in ("name", "arguments"):
                            entry["function"][key] += (call.get("function") or {}).get(key) or ""
            if finish is None or finish == "length":
                raise ValueError("benchmark_stream_truncated")
            message = {"role": "assistant", "content": content or None}
            if calls:
                message["tool_calls"] = [calls[i] for i in sorted(calls)]
            return {"model": model, "choices": [{"message": message}]}, {
                "ttft_ms": first,
                "response_ms": (time.monotonic() - started) * 1000,
                "usage": usage,
            }


def gpu_snapshot():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--gguf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--require-gpu", action="store_true")
    args = parser.parse_args()
    gpu = gpu_snapshot()
    if args.require_gpu and gpu is None:
        report = {
            "schema": "ananta.multi-tool-benchmark.v1",
            "status": "blocked",
            "reason": "gpu_unavailable",
            "classification": "technical_observation",
            "production_release_evidence": False,
        }
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        return 2
    digest = hashlib.sha256()
    with args.gguf.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    transport = LocalBenchmarkTransport(args.url)

    # Both routes are on this exact server/model alias; native model output must
    # carry the same alias. No alternate cloud client or model can be a fallback.
    def measured(path, body, deadline):
        payload, metrics = transport(path, body, deadline)
        if payload.get("model") != args.model:
            raise ValueError("benchmark_model_mismatch")
        metrics["gpu_memory_snapshot"] = gpu_snapshot() if gpu else None
        return payload, metrics

    report = run_comparison(
        measured,
        model=args.model,
        model_sha256=digest.hexdigest(),
        hardware={
            "gpu_before": gpu,
            "vram_measurement": "per-response snapshot, not kernel peak" if gpu else "unavailable",
        },
        repeats=args.repeats,
        timeout_seconds=args.timeout,
    )
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": "completed", "decision": report["decision"], "rows": len(report["rows"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
