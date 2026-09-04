"""Bounded, Hub-evidence-aware helpers for local model evaluations."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ananta_contracts.hub_evidence import (
    HubEvidenceAssignmentError,
    validate_hub_evidence_assignment,
)


class OrnithBenchmarkError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResourceSample:
    monotonic_seconds: float
    gpu_memory_used_mib: int | None
    gpu_temperature_c: int | None
    ram_available_bytes: int
    swap_used_bytes: int


def load_hub_assignment() -> dict[str, Any] | None:
    raw = os.environ.get("ANANTA_HUB_EVIDENCE_ASSIGNMENT", "").strip()
    if not raw:
        return None
    try:
        return validate_hub_evidence_assignment(json.loads(raw))
    except (json.JSONDecodeError, HubEvidenceAssignmentError) as exc:
        raise OrnithBenchmarkError("ornith_hub_assignment_invalid") from exc


def evidence_projection(assignment: dict[str, Any] | None) -> dict[str, Any]:
    if assignment is None:
        return {"state": "unverified", "reason_code": "hub_evidence_assignment_missing"}
    return {
        "state": "hub_bound",
        "run_id": assignment["run_id"],
        "source_ids": assignment["source_ids"],
        "scope": assignment["evidence_scope"],
        "assignment_id": assignment["assignment_id"],
        "dispatch_lease_id": assignment["dispatch_lease_id"],
    }


def require_loopback_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise OrnithBenchmarkError("ornith_endpoint_not_loopback")
    return endpoint.rstrip("/")


def sample_resources() -> ResourceSample:
    fields: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(":")
        if key in {"MemAvailable", "SwapTotal", "SwapFree"}:
            fields[key] = int(value.strip().split()[0]) * 1024
    gpu_memory: int | None = None
    gpu_temp: int | None = None
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        values = [int(value.strip()) for value in result.stdout.splitlines()[0].split(",")]
        gpu_memory, gpu_temp = values
    except (FileNotFoundError, IndexError, ValueError, subprocess.SubprocessError):
        pass
    return ResourceSample(
        monotonic_seconds=time.monotonic(),
        gpu_memory_used_mib=gpu_memory,
        gpu_temperature_c=gpu_temp,
        ram_available_bytes=fields.get("MemAvailable", 0),
        swap_used_bytes=max(0, fields.get("SwapTotal", 0) - fields.get("SwapFree", 0)),
    )


def call_openai_chat(
    endpoint: str,
    *,
    model: str,
    prompt: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    url = f"{require_loopback_endpoint(endpoint)}/v1/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "stream": False,
        },
        separators=(",", ":"),
    ).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read(8 * 1024 * 1024))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OrnithBenchmarkError("ornith_runtime_request_failed") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list):
        raise OrnithBenchmarkError("ornith_runtime_response_invalid")
    content = str(payload["choices"][0].get("message", {}).get("content") or "")
    return {
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "response_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "response_characters": len(content),
        "usage": payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
    }


def enforce_resource_safety(
    before: ResourceSample,
    after: ResourceSample,
    *,
    maximum_temperature_c: int = 85,
) -> None:
    if after.swap_used_bytes > before.swap_used_bytes:
        raise OrnithBenchmarkError("ornith_swap_growth_detected")
    if after.gpu_temperature_c is not None and after.gpu_temperature_c >= maximum_temperature_c:
        raise OrnithBenchmarkError("ornith_thermal_limit_reached")


def resource_dict(sample: ResourceSample) -> dict[str, Any]:
    return asdict(sample)


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = [
    "OrnithBenchmarkError",
    "call_openai_chat",
    "enforce_resource_safety",
    "evidence_projection",
    "load_hub_assignment",
    "require_loopback_endpoint",
    "resource_dict",
    "sample_resources",
    "write_report",
]
