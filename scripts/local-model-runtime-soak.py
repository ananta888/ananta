#!/usr/bin/env python3
"""Real KAT/LFM/Needle parallel-soak collector and fail-closed gate."""

from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import json
import math
import os
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

MINIMUM_SOAK_SECONDS = 1800
DEFAULT_OUTPUT = Path("artifacts/local-model-runtime-soak.json")
_OPENAI_MODELS = {
    "kat": "kat-coder-v2.5-dev",
    "lfm": "lfm2.5-2.6b-agentic-q8_0",
}


class SoakGateError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered or not 0.0 <= quantile <= 1.0:
        raise ValueError("local_runtime_soak_percentile_invalid")
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


def summarize(values: Iterable[float]) -> dict[str, float]:
    rows = tuple(float(value) for value in values)
    if not rows or any(not math.isfinite(value) or value < 0.0 for value in rows):
        raise ValueError("local_runtime_soak_measurement_invalid")
    return {
        "p50": round(percentile(rows, 0.50), 6),
        "p95": round(percentile(rows, 0.95), 6),
        "p99": round(percentile(rows, 0.99), 6),
    }


def validate_report(report: Mapping[str, Any], *, minimum_duration_seconds: int) -> tuple[str, ...]:
    reasons: list[str] = []
    minimum_samples = max(1, int(report.get("minimum_samples_per_runtime") or 1))
    if report.get("scope") != "real_local_runtime":
        reasons.append("real_runtime_scope_missing")
    if float(report.get("duration_seconds") or 0.0) < minimum_duration_seconds:
        reasons.append("soak_duration_too_short")
    runtimes = report.get("runtimes")
    if not isinstance(runtimes, Mapping) or set(runtimes) != {"kat", "lfm", "needle"}:
        reasons.append("runtime_measurements_incomplete")
    else:
        for runtime_id, metrics in runtimes.items():
            if not isinstance(metrics, Mapping) or int(metrics.get("successes") or 0) < minimum_samples:
                reasons.append(f"{runtime_id}_success_missing")
                continue
            if int(metrics.get("failures") or 0):
                reasons.append(f"{runtime_id}_failure_observed")
            required: tuple[str, ...] = ("latency_ms", "throughput_per_second")
            if runtime_id in _OPENAI_MODELS:
                required = (*required, "ttft_ms", "prompt_tokens")
            for key in required:
                summary = metrics.get(key)
                if not isinstance(summary, Mapping) or any(name not in summary for name in ("p50", "p95", "p99")):
                    reasons.append(f"{runtime_id}_{key}_missing")
            if runtime_id in _OPENAI_MODELS:
                prompt_summary = metrics.get("prompt_tokens")
                observed_prompt_tokens = (
                    float(prompt_summary.get("p50") or 0) if isinstance(prompt_summary, Mapping) else 0.0
                )
                if observed_prompt_tokens < int(report.get("minimum_prompt_tokens") or 0):
                    reasons.append(f"{runtime_id}_long_context_missing")
            if runtime_id == "kat" and metrics.get("expert_hit_rate") is None:
                reasons.append("kat_expert_hit_rate_missing")
    resources = report.get("resources")
    if not isinstance(resources, Mapping) or int(resources.get("samples") or 0) < 2:
        reasons.append("resource_correlation_missing")
    else:
        if not all(
            isinstance(resources.get(key), Mapping) and set(resources[key]) == {"kat", "lfm", "needle"}
            for key in ("maximum_rss_bytes", "cpu_time_tick_delta")
        ):
            reasons.append("runtime_ram_cpu_correlation_missing")
        else:
            if any(int(value) <= 0 for value in resources["maximum_rss_bytes"].values()):
                reasons.append("runtime_ram_correlation_invalid")
            if any(int(value) <= 0 for value in resources["cpu_time_tick_delta"].values()):
                reasons.append("runtime_cpu_correlation_invalid")
        if int(resources.get("minimum_free_vram_bytes") or 0) < int(report.get("required_reserve_vram_bytes") or 0):
            reasons.append("vram_reserve_violated")
        if bool(resources.get("process_missing")):
            reasons.append("runtime_process_missing")
    return tuple(reasons)


class SoakCollector:
    def __init__(
        self,
        *,
        endpoints: Mapping[str, str],
        model_token: str,
        needle_token: str,
        state_dir: Path,
        request_timeout_seconds: float,
        prompt_chars: int,
        minimum_prompt_tokens: int,
    ) -> None:
        if len(model_token) < 24 or len(needle_token) < 24:
            raise SoakGateError("local_runtime_soak_token_missing")
        self._endpoints = {key: _endpoint(value, runtime_id=key) for key, value in endpoints.items()}
        if set(self._endpoints) != {"kat", "lfm", "needle"}:
            raise SoakGateError("local_runtime_soak_endpoint_set_invalid")
        self._model_token = model_token
        self._needle_token = needle_token
        self._state_dir = state_dir
        self._timeout = max(1.0, min(float(request_timeout_seconds), 600.0))
        self._prompt = ("x " * (prompt_chars // 2 + 1))[:prompt_chars]
        self._prompt_characters = len(self._prompt)
        self._minimum_prompt_tokens = max(1, int(minimum_prompt_tokens))
        self._opener = urllib.request.build_opener(_NoRedirect)
        self._measurement_epoch = time.monotonic()

    def collect(
        self,
        *,
        duration_seconds: int,
        interval_seconds: float,
        reserve_vram_bytes: int,
        minimum_samples: int = 2,
        resource_interval_seconds: float = 5.0,
    ) -> dict[str, Any]:
        started_wall = datetime.now(UTC)
        started = time.monotonic()
        measurements: dict[str, list[dict[str, float]]] = {key: [] for key in self._endpoints}
        failures: dict[str, list[str]] = {key: [] for key in self._endpoints}
        resources: list[dict[str, Any]] = []
        attempts = 0
        required_samples = max(1, int(minimum_samples))
        resource_interval = max(0.1, min(float(resource_interval_seconds), 60.0))
        while True:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = {
                    "kat": executor.submit(self._openai_sample, "kat"),
                    "lfm": executor.submit(self._openai_sample, "lfm"),
                    "needle": executor.submit(self._needle_sample),
                }
                pending = set(futures.values())
                while pending:
                    resources.append(self._resources())
                    _, pending = concurrent.futures.wait(
                        pending,
                        timeout=resource_interval,
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                for runtime_id, future in futures.items():
                    try:
                        measurements[runtime_id].append(future.result())
                    except Exception as exc:
                        failures[runtime_id].append(_reason(exc))
            attempts += 1
            elapsed = time.monotonic() - started
            if elapsed >= duration_seconds and attempts >= required_samples:
                break
            time.sleep(min(max(0.1, interval_seconds), max(0.0, duration_seconds - elapsed)))
        resources.append(self._resources())
        duration = time.monotonic() - started
        report: dict[str, Any] = {
            "schema": "ananta.local-model-runtime-soak.v1",
            "scope": "real_local_runtime",
            "started_at": started_wall.isoformat().replace("+00:00", "Z"),
            "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "duration_seconds": round(duration, 6),
            "minimum_prompt_tokens": self._minimum_prompt_tokens,
            "prompt_characters": self._prompt_characters,
            "minimum_samples_per_runtime": required_samples,
            "required_reserve_vram_bytes": reserve_vram_bytes,
            "runtimes": {
                runtime_id: self._runtime_summary(runtime_id, rows, failures[runtime_id])
                for runtime_id, rows in measurements.items()
            },
            "resources": self._resource_summary(resources),
        }
        reasons = validate_report(report, minimum_duration_seconds=duration_seconds)
        report["passed"] = not reasons
        report["reason_codes"] = list(reasons)
        return report

    def _openai_sample(self, runtime_id: str) -> dict[str, float]:
        endpoint = self._endpoints[runtime_id]
        body = {
            "model": _OPENAI_MODELS[runtime_id],
            "messages": [{"role": "user", "content": self._prompt}],
            "max_tokens": 32,
            "temperature": 0,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        started = time.monotonic()
        response = self._request(endpoint + "/v1/chat/completions", body, self._model_token)
        first_content_at: float | None = None
        completion_tokens = 0
        prompt_tokens = 0
        hit_rates: list[float] = []
        chunks = 0
        with response:
            for raw in response:
                line = raw.decode("utf-8", errors="strict").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                row = json.loads(payload)
                content = _stream_content(row)
                if content:
                    chunks += 1
                    first_content_at = first_content_at or time.monotonic()
                usage = row.get("usage") if isinstance(row, Mapping) else None
                if isinstance(usage, Mapping):
                    completion_tokens = max(completion_tokens, int(usage.get("completion_tokens") or 0))
                    prompt_tokens = max(prompt_tokens, int(usage.get("prompt_tokens") or 0))
                hit_rates.extend(_hit_rates(row))
        ended = time.monotonic()
        if first_content_at is None or chunks < 1:
            raise SoakGateError("provider_stream_content_missing")
        if prompt_tokens < 1:
            raise SoakGateError("provider_prompt_tokens_missing")
        tokens = completion_tokens or chunks
        generation_seconds = max(0.000001, ended - first_content_at)
        result = {
            "latency_ms": (ended - started) * 1000.0,
            "ttft_ms": (first_content_at - started) * 1000.0,
            "throughput_per_second": tokens / generation_seconds,
            "prompt_tokens": float(prompt_tokens),
        }
        if hit_rates:
            result["expert_hit_rate"] = statistics.fmean(hit_rates)
        return result

    def _needle_sample(self) -> dict[str, float]:
        tool = {
            "type": "function",
            "function": {
                "name": "status_lookup",
                "description": "Read a public status value",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            },
        }
        started = time.monotonic()
        response = self._request(
            self._endpoints["needle"] + "/internal/v1/candidates",
            {"prompt": "Read the public runtime status", "tools": [tool]},
            self._needle_token,
        )
        with response:
            payload = json.loads(response.read().decode("utf-8"))
        ended = time.monotonic()
        if not isinstance(payload, Mapping) or not isinstance(payload.get("candidate"), Mapping):
            raise SoakGateError("needle_candidate_invalid")
        latency = max(0.000001, ended - started)
        return {"latency_ms": latency * 1000.0, "throughput_per_second": 1.0 / latency}

    def _request(self, url: str, payload: Mapping[str, Any], token: str):
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Host": "host.docker.internal",
            },
            method="POST",
        )
        return self._opener.open(request, timeout=self._timeout)

    def _resources(self) -> dict[str, Any]:
        completed = subprocess.run(
            [
                "/usr/bin/nvidia-smi",
                "--query-gpu=memory.used,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        gpu = [int(value.strip()) for value in completed.stdout.splitlines()[0].split(",")]
        rss: dict[str, int] = {}
        cpu_ticks: dict[str, int] = {}
        missing = False
        for runtime_id in ("kat", "lfm", "needle"):
            try:
                pid = int((self._state_dir / f"{runtime_id}.pid").read_text().strip())
                process_ids = _descendant_pids(pid)
                rss[runtime_id] = sum(_rss_bytes(process_id) for process_id in process_ids)
                cpu_ticks[runtime_id] = sum(_cpu_ticks(process_id) for process_id in process_ids)
            except (OSError, ValueError, StopIteration):
                missing = True
                rss[runtime_id] = 0
                cpu_ticks[runtime_id] = 0
        return {
            "observed_offset_seconds": time.monotonic() - self._measurement_epoch,
            "vram_used_bytes": gpu[0] * 1024 * 1024,
            "free_vram_bytes": gpu[1] * 1024 * 1024,
            "gpu_utilization_percent": gpu[2],
            "rss_bytes": rss,
            "cpu_time_ticks": cpu_ticks,
            "process_missing": missing,
        }

    @staticmethod
    def _runtime_summary(runtime_id: str, rows: list[dict[str, float]], failures: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "successes": len(rows),
            "failures": len(failures),
            "failure_reason_codes": sorted(set(failures)),
        }
        for key in ("latency_ms", "ttft_ms", "throughput_per_second", "prompt_tokens"):
            values = [row[key] for row in rows if key in row]
            if values:
                result[key] = summarize(values)
        hits = [row["expert_hit_rate"] for row in rows if "expert_hit_rate" in row]
        result["expert_hit_rate"] = round(statistics.fmean(hits), 6) if hits else None
        return result

    @staticmethod
    def _resource_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "samples": len(rows),
            "minimum_free_vram_bytes": min(int(row["free_vram_bytes"]) for row in rows),
            "maximum_vram_used_bytes": max(int(row["vram_used_bytes"]) for row in rows),
            "maximum_gpu_utilization_percent": max(int(row["gpu_utilization_percent"]) for row in rows),
            "maximum_rss_bytes": {
                runtime_id: max(int(row["rss_bytes"][runtime_id]) for row in rows)
                for runtime_id in ("kat", "lfm", "needle")
            },
            "cpu_time_tick_delta": {
                runtime_id: max(
                    0, int(rows[-1]["cpu_time_ticks"][runtime_id]) - int(rows[0]["cpu_time_ticks"][runtime_id])
                )
                for runtime_id in ("kat", "lfm", "needle")
            },
            "process_missing": any(bool(row["process_missing"]) for row in rows),
        }


def _endpoint(value: str, *, runtime_id: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    hostname = parsed.hostname or ""
    try:
        private_ip = ipaddress.ip_address(hostname).is_private
    except ValueError:
        private_ip = False
    if (
        parsed.scheme != "http"
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ({"", "/"} if runtime_id == "needle" else {"", "/", "/v1", "/v1/"})
        or parsed.query
        or parsed.fragment
        or (hostname not in {"localhost", "host.docker.internal"} and not private_ip)
    ):
        raise SoakGateError("local_runtime_soak_endpoint_invalid")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _descendant_pids(root_pid: int, *, proc_root: Path = Path("/proc")) -> frozenset[int]:
    parents: dict[int, int] = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="ascii")
            fields = stat[stat.rfind(")") + 2 :].split()
            parents[int(entry.name)] = int(fields[1])
        except (OSError, UnicodeError, ValueError, IndexError):
            continue
    if root_pid not in parents:
        raise OSError("runtime process is unavailable")
    result = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in result and pid not in result:
                result.add(pid)
                changed = True
    return frozenset(result)


def _rss_bytes(pid: int) -> int:
    status = Path(f"/proc/{pid}/status").read_text(encoding="ascii")
    return next(int(line.split()[1]) * 1024 for line in status.splitlines() if line.startswith("VmRSS:"))


def _cpu_ticks(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    fields = stat[stat.rfind(")") + 2 :].split()
    return int(fields[11]) + int(fields[12])


def _stream_content(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return ""
    delta = choices[0].get("delta")
    if not isinstance(delta, Mapping):
        return ""
    return str(delta.get("content") or delta.get("reasoning_content") or "")


def _hit_rates(value: Any) -> list[float]:
    result: list[float] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in {
                "expert_cache_hit_rate",
                "expert_hit_rate",
                "expert_hitrate",
                "cache_hit_rate",
            }:
                try:
                    numeric = float(child)
                except (TypeError, ValueError):
                    continue
                if 1.0 < numeric <= 100.0:
                    numeric /= 100.0
                if 0.0 <= numeric <= 1.0:
                    result.append(numeric)
            else:
                result.extend(_hit_rates(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(_hit_rates(child))
    return result


def _reason(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, urllib.error.URLError)) and "timed out" in str(exc).lower():
        return "runtime_timeout"
    if isinstance(exc, urllib.error.HTTPError):
        return f"runtime_http_{exc.code}"
    if isinstance(exc, SoakGateError):
        return str(exc)
    return "runtime_request_failed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, default=MINIMUM_SOAK_SECONDS)
    parser.add_argument("--minimum-duration-seconds", type=int, default=MINIMUM_SOAK_SECONDS)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--prompt-chars", type=int, default=32_000)
    parser.add_argument("--minimum-prompt-tokens", type=int, default=16_000)
    parser.add_argument("--minimum-samples", type=int, default=2)
    parser.add_argument("--resource-interval-seconds", type=float, default=5.0)
    parser.add_argument("--reserve-vram-mib", type=int, default=1536)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.minimum_duration_seconds < MINIMUM_SOAK_SECONDS or args.duration_seconds < args.minimum_duration_seconds:
        raise SystemExit("local_runtime_soak_duration_below_gate")
    collector = SoakCollector(
        endpoints={
            "kat": os.getenv("ANANTA_KAT_ENDPOINT", "http://127.0.0.1:8082"),
            "lfm": os.getenv("ANANTA_LFM_ENDPOINT", "http://127.0.0.1:8081"),
            "needle": os.getenv("ANANTA_NEEDLE_ENDPOINT", "http://127.0.0.1:8083"),
        },
        model_token=os.getenv("ANANTA_LOCAL_MODEL_API_KEY", ""),
        needle_token=os.getenv("ANANTA_NEEDLE_TOKEN", "") or os.getenv("ANANTA_LOCAL_MODEL_API_KEY", ""),
        state_dir=Path(os.getenv("ANANTA_LOCAL_MODEL_STATE_DIR", "data/local-model-runtime")),
        request_timeout_seconds=args.request_timeout_seconds,
        prompt_chars=max(128, min(args.prompt_chars, 500_000)),
        minimum_prompt_tokens=max(1, min(args.minimum_prompt_tokens, 32_000)),
    )
    report = collector.collect(
        duration_seconds=args.duration_seconds,
        interval_seconds=args.interval_seconds,
        reserve_vram_bytes=args.reserve_vram_mib * 1024 * 1024,
        minimum_samples=max(1, min(args.minimum_samples, 1000)),
        resource_interval_seconds=args.resource_interval_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "reason_codes": report["reason_codes"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
