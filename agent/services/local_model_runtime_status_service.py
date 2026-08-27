"""Hub application service for content-free local runtime status snapshots."""

from __future__ import annotations

import ipaddress
import json
import math
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Protocol, Sequence, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from agent.services.local_multi_model_runtime import (
    LocalModelCapability,
    MiB,
    ResourceSnapshot,
    RuntimeId,
    RuntimeResourceMeasurement,
)
from ananta_contracts.local_model_runtime import (
    LocalRuntimeResourceUsage,
    LocalRuntimeSnapshot,
    LocalRuntimeStatus,
    RuntimeHealth,
    RuntimeReadiness,
)


@dataclass(frozen=True, slots=True)
class RuntimeProbeObservation:
    health: RuntimeHealth
    readiness: RuntimeReadiness
    reason_code: str
    listed_model_ids: tuple[str, ...] = ()


class LocalRuntimeProbePort(Protocol):
    def probe(self, capability: LocalModelCapability, *, timeout_seconds: float) -> RuntimeProbeObservation: ...


class LocalResourceSnapshotPort(Protocol):
    def snapshot(self) -> ResourceSnapshot: ...


class _ReadableHttpResponse(Protocol):
    status: int

    def read(self) -> bytes: ...


class HttpLocalRuntimeProbe:
    """Small HTTP adapter; it never invokes a model or sends prompt content."""

    def __init__(
        self,
        *,
        token_resolver: Callable[[str], str | None] | None = None,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self._token_resolver = token_resolver or _runtime_token
        self._opener = opener

    def probe(self, capability: LocalModelCapability, *, timeout_seconds: float) -> RuntimeProbeObservation:
        endpoint = str(capability.endpoint or "").rstrip("/")
        if not endpoint:
            return RuntimeProbeObservation(
                RuntimeHealth.UNKNOWN,
                RuntimeReadiness.UNKNOWN,
                "runtime_endpoint_unconfigured",
            )
        url = f"{endpoint}/ready" if capability.runtime_id == "needle" else f"{endpoint}/models"
        headers = {"Accept": "application/json"}
        token = str(self._token_resolver(capability.runtime_id) or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, headers=headers, method="GET")
        try:
            response = cast(
                _ReadableHttpResponse,
                self._opener(request, timeout=max(0.05, min(float(timeout_seconds), 5.0))),
            )
            status = int(getattr(response, "status", 200) or 200)
            payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return RuntimeProbeObservation(
                RuntimeHealth.UNAVAILABLE,
                RuntimeReadiness.NOT_READY,
                "runtime_probe_unauthorized" if exc.code in {401, 403} else "runtime_probe_http_error",
            )
        except TimeoutError:
            return RuntimeProbeObservation(
                RuntimeHealth.DEGRADED,
                RuntimeReadiness.NOT_READY,
                "runtime_probe_timeout",
            )
        except (URLError, OSError):
            return RuntimeProbeObservation(
                RuntimeHealth.UNAVAILABLE,
                RuntimeReadiness.NOT_READY,
                "runtime_probe_unreachable",
            )
        except (UnicodeError, json.JSONDecodeError, AttributeError, TypeError, ValueError):
            return RuntimeProbeObservation(
                RuntimeHealth.DEGRADED,
                RuntimeReadiness.NOT_READY,
                "runtime_probe_response_invalid",
            )
        if status != 200 or not isinstance(payload, dict):
            return RuntimeProbeObservation(
                RuntimeHealth.DEGRADED,
                RuntimeReadiness.NOT_READY,
                "runtime_probe_not_ready",
            )
        if capability.runtime_id == "needle":
            ready = payload.get("ready") is True or str(payload.get("status") or "").lower() in {"ok", "ready"}
            return RuntimeProbeObservation(
                RuntimeHealth.HEALTHY if ready else RuntimeHealth.DEGRADED,
                RuntimeReadiness.READY if ready else RuntimeReadiness.NOT_READY,
                "runtime_ready" if ready else "runtime_probe_not_ready",
                (capability.model_id,) if ready else (),
            )
        rows = payload.get("data")
        model_ids = (
            tuple(
                sorted(
                    str(item.get("id") or "").strip().lower()
                    for item in rows
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                )
            )
            if isinstance(rows, list)
            else ()
        )
        ready = capability.model_id.lower() in model_ids
        return RuntimeProbeObservation(
            RuntimeHealth.HEALTHY if ready else RuntimeHealth.DEGRADED,
            RuntimeReadiness.READY if ready else RuntimeReadiness.NOT_READY,
            "runtime_ready" if ready else "runtime_model_not_loaded",
            model_ids,
        )


class SystemLocalResourceSnapshot:
    """Bounded infrastructure adapter for NVIDIA and Linux memory facts."""

    def __init__(self, *, command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self._command_runner = command_runner

    def snapshot(self) -> ResourceSnapshot:
        try:
            result = self._command_runner(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
            first_line = str(result.stdout or "").splitlines()[0]
            total_mib, free_mib = (int(value.strip()) for value in first_line.split(",", 1))
        except (FileNotFoundError, IndexError, OSError, ValueError, subprocess.SubprocessError) as exc:
            raise RuntimeError("local_runtime_gpu_snapshot_unavailable") from exc
        available_ram_bytes = _available_ram_bytes()
        return ResourceSnapshot(
            total_vram_bytes=total_mib * MiB,
            free_vram_bytes=free_mib * MiB,
            available_ram_bytes=available_ram_bytes,
        )


class HttpLocalResourceSnapshot:
    """Reads content-free resource facts from the allowlisted operator bridge."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        self._base_url = _internal_base_url(base_url)
        if len(str(token or "")) < 24:
            raise ValueError("local_runtime_control_token_invalid")
        self._token = str(token)
        self._opener = opener

    def snapshot(self) -> ResourceSnapshot:
        request = Request(
            f"{self._base_url}/v1/resources",
            headers={"Accept": "application/json", "Authorization": f"Bearer {self._token}"},
            method="GET",
        )
        try:
            response = cast(_ReadableHttpResponse, self._opener(request, timeout=3))
            if int(getattr(response, "status", 200) or 200) != 200:
                raise RuntimeError("local_runtime_resource_bridge_unavailable")
            payload = json.loads(response.read().decode("utf-8"))
            return ResourceSnapshot(
                total_vram_bytes=int(payload["total_vram_bytes"]),
                free_vram_bytes=int(payload["free_vram_bytes"]),
                available_ram_bytes=int(payload["available_ram_bytes"]),
                runtime_usage=_runtime_usage(payload.get("runtime_usage")),
                active_contexts=_active_contexts(payload.get("effective_contexts")),
            )
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise RuntimeError("local_runtime_resource_bridge_unavailable") from exc


class LocalRuntimeStatusService:
    """Projects injected observations into one closed operator read model."""

    def __init__(
        self,
        *,
        probes: LocalRuntimeProbePort,
        resources: LocalResourceSnapshotPort,
        clock: Callable[[], datetime] | None = None,
        reserve_vram_bytes: int = 1536 * MiB,
        timeout_seconds: float = 1.0,
    ) -> None:
        if isinstance(reserve_vram_bytes, bool) or not isinstance(reserve_vram_bytes, int) or reserve_vram_bytes < 0:
            raise ValueError("local_runtime_vram_reserve_invalid")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or float(timeout_seconds) <= 0.0
        ):
            raise ValueError("local_runtime_probe_timeout_invalid")
        self._probes = probes
        self._resources = resources
        self._clock = clock or (lambda: datetime.now(UTC))
        self._reserve_vram_bytes = reserve_vram_bytes
        self._timeout_seconds = max(0.05, min(float(timeout_seconds), 5.0))

    def snapshot(
        self,
        capabilities: Sequence[LocalModelCapability],
        *,
        revision: int = 1,
        effective_contexts: Mapping[RuntimeId, int] | None = None,
    ) -> LocalRuntimeSnapshot:
        by_id = {capability.runtime_id: capability for capability in capabilities}
        if set(by_id) != {"kat", "lfm", "needle"} or len(capabilities) != 3:
            raise ValueError("local_runtime_capability_set_incomplete")
        resource_snapshot = self._resources.snapshot()
        contexts = dict(resource_snapshot.active_contexts)
        contexts.update(effective_contexts or {})
        statuses = tuple(
            self._status(
                by_id[runtime_id],
                revision=revision,
                effective_context=int(contexts.get(runtime_id) or by_id[runtime_id].default_context),
                resource_snapshot=resource_snapshot,
            )
            for runtime_id in ("kat", "lfm", "needle")
        )
        return LocalRuntimeSnapshot(
            revision=revision,
            generated_at=self._clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
            total_vram_bytes=resource_snapshot.total_vram_bytes,
            free_vram_bytes=resource_snapshot.free_vram_bytes,
            available_ram_bytes=resource_snapshot.available_ram_bytes,
            reserve_vram_bytes=self._reserve_vram_bytes,
            runtimes=statuses,
        )

    def _status(
        self,
        capability: LocalModelCapability,
        *,
        revision: int,
        effective_context: int,
        resource_snapshot: ResourceSnapshot,
    ) -> LocalRuntimeStatus:
        observation = self._probes.probe(capability, timeout_seconds=self._timeout_seconds)
        candidate_only = capability.runtime_id == "needle"
        measurement = resource_snapshot.runtime_usage.get(capability.runtime_id)
        exceeded = measurement is not None and (
            (capability.vram_budget_bytes and measurement.vram_used_bytes > capability.vram_budget_bytes)
            or (capability.ram_budget_bytes and measurement.ram_used_bytes > capability.ram_budget_bytes)
        )
        return LocalRuntimeStatus(
            snapshot_revision=revision,
            runtime_id=capability.runtime_id,
            provider_id=capability.provider_id,
            model_id=capability.model_id,
            execution_device=capability.execution_device,
            health=observation.health,
            readiness=observation.readiness,
            reason_code=observation.reason_code,
            effective_context=effective_context,
            context_capacity=capability.context_capacity,
            capabilities=tuple(capability.capabilities),
            available_models=observation.listed_model_ids,
            resources=LocalRuntimeResourceUsage(
                vram_used_bytes=measurement.vram_used_bytes if measurement is not None else 0,
                vram_budget_bytes=capability.vram_budget_bytes,
                ram_used_bytes=measurement.ram_used_bytes if measurement is not None else 0,
                ram_budget_bytes=capability.ram_budget_bytes,
                budget_status=("exceeded" if exceeded else "within_budget")
                if measurement is not None
                else "unmeasured",
            ),
            timeout_supported=True,
            # Cancellation fences all returned provider/candidate results. It
            # does not claim that an upstream server stopped computing early.
            cancellation_supported=True,
            candidate_only=candidate_only,
        )


def _runtime_token(runtime_id: str) -> str | None:
    if runtime_id == "needle":
        return os.environ.get("ANANTA_NEEDLE_TOKEN") or os.environ.get("ANANTA_LOCAL_MODEL_API_KEY")
    return os.environ.get("ANANTA_LOCAL_MODEL_API_KEY")


def _runtime_usage(value: Any) -> Mapping[RuntimeId, RuntimeResourceMeasurement]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("local_runtime_resource_usage_invalid")
    result: dict[RuntimeId, RuntimeResourceMeasurement] = {}
    for runtime_id, row in value.items():
        normalized_id = str(runtime_id or "").strip().lower()
        if normalized_id not in {"kat", "lfm", "needle"} or not isinstance(row, Mapping):
            raise ValueError("local_runtime_resource_usage_invalid")
        if set(row) != {"vram_used_bytes", "ram_used_bytes"}:
            raise ValueError("local_runtime_resource_usage_invalid")
        result[cast(RuntimeId, normalized_id)] = RuntimeResourceMeasurement(
            vram_used_bytes=int(row["vram_used_bytes"]),
            ram_used_bytes=int(row["ram_used_bytes"]),
        )
    return result


def _active_contexts(value: Any) -> Mapping[RuntimeId, int]:
    if value is None or value == {}:
        return {}
    if not isinstance(value, Mapping) or set(value) != {"kat", "lfm", "needle"}:
        raise ValueError("local_runtime_active_context_set_invalid")
    if any(isinstance(context, bool) or not isinstance(context, int) for context in value.values()):
        raise ValueError("local_runtime_active_context_invalid")
    result: dict[RuntimeId, int] = {cast(RuntimeId, str(runtime_id)): context for runtime_id, context in value.items()}
    if any(context < 1 or context > 100_000_000 for context in result.values()):
        raise ValueError("local_runtime_active_context_invalid")
    return result


def _internal_base_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    try:
        private_ip = ipaddress.ip_address(parsed.hostname or "").is_private
    except ValueError:
        private_ip = False
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.hostname not in {"127.0.0.1", "::1", "localhost", "host.docker.internal"} and not private_ip)
        or parsed.port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("local_runtime_control_url_invalid")
    return str(value).rstrip("/")


def _available_ram_bytes() -> int:
    try:
        for line in open("/proc/meminfo", encoding="ascii"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, IndexError, ValueError):
        pass
    raise RuntimeError("local_runtime_ram_snapshot_unavailable")


__all__ = [
    "HttpLocalRuntimeProbe",
    "HttpLocalResourceSnapshot",
    "LocalResourceSnapshotPort",
    "LocalRuntimeProbePort",
    "LocalRuntimeStatusService",
    "RuntimeProbeObservation",
    "SystemLocalResourceSnapshot",
]
