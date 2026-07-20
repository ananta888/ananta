"""Registered Worker adapter for one Hub-delegated semantic-compute task."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from typing import Any, Mapping

from ananta_contracts.file_credentials import read_file_managed_token
from ananta_contracts.semantic_compute import SemanticComputeWorkerTask
from worker.semantic_media.handler import (
    SemanticComputeWorkerError,
    SemanticComputeWorkerHandler,
    WorkerArtifact,
)


class SemanticComputeWorkerConfigurationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SemanticComputeHubClientError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class HttpSemanticComputeHubClient:
    """Least-privilege Worker transport for authorization and artifacts."""

    def __init__(
        self,
        *,
        hub_url: str,
        bearer_token: str,
        worker_id: str,
        worker_url: str,
        timeout_seconds: float = 15.0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(str(hub_url or "").rstrip("/"))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SemanticComputeWorkerConfigurationError("semantic_compute_hub_url_invalid")
        if not worker_id or not worker_url:
            raise SemanticComputeWorkerConfigurationError("semantic_compute_worker_identity_missing")
        if not 32 <= len(bearer_token.encode()) <= 16_384:
            raise SemanticComputeWorkerConfigurationError("semantic_compute_worker_token_invalid")
        self._hub_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self._token = bearer_token
        self._worker_id = worker_id
        self._worker_url = worker_url.rstrip("/")
        self._timeout = max(1.0, min(float(timeout_seconds), 60.0))
        self._ssl_context = ssl_context

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "HttpSemanticComputeHubClient":
        source = os.environ if env is None else env
        hub_url = str(
            source.get("ANANTA_SEMANTIC_COMPUTE_HUB_URL")
            or source.get("ANANTA_WORKFLOW_HUB_URL")
            or source.get("HUB_URL")
            or ""
        ).strip()
        token_file = str(
            source.get("ANANTA_SEMANTIC_COMPUTE_HUB_TOKEN_FILE")
            or source.get("ANANTA_WORKFLOW_HUB_TOKEN_FILE")
            or source.get("AGENT_TOKEN_FILE")
            or ""
        ).strip()
        if not hub_url or not token_file:
            raise SemanticComputeWorkerConfigurationError("semantic_compute_hub_not_configured")
        try:
            token = read_file_managed_token(token_file, description="semantic compute Hub token file")
        except Exception as exc:
            raise SemanticComputeWorkerConfigurationError("semantic_compute_worker_token_unavailable") from exc
        return cls(
            hub_url=hub_url,
            bearer_token=token,
            worker_id=str(source.get("AGENT_NAME") or "").strip(),
            worker_url=str(source.get("AGENT_URL") or "").strip(),
        )

    def authorized(self, task: SemanticComputeWorkerTask) -> bool:
        try:
            response = self._post("authorize", {"task": task.to_dict()}, 196_608)
        except SemanticComputeHubClientError:
            return False
        return response.get("authorized") is True and response.get("task_id") == task.task_id

    def read_input(self, task: SemanticComputeWorkerTask, input_ref: str) -> tuple[bytes, str]:
        response = self._post("inputs", {"task": task.to_dict(), "input_ref": input_ref}, 22 * 1024 * 1024)
        try:
            content = base64.b64decode(str(response["content_b64"]), validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticComputeHubClientError("semantic_compute_input_invalid") from exc
        if int(response.get("size_bytes") or -1) != len(content):
            raise SemanticComputeHubClientError("semantic_compute_input_size_mismatch")
        return content, str(response.get("media_type") or "application/octet-stream")

    def publish(self, task: SemanticComputeWorkerTask, content: bytes) -> str:
        response = self._post(
            "artifacts",
            {
                "task": task.to_dict(),
                "publish_ref": task.artifact_publish_ref,
                "content_b64": base64.b64encode(content).decode("ascii"),
            },
            262_144,
        )
        reference = str(response.get("artifact_ref") or "")
        if not reference.startswith("artifact:"):
            raise SemanticComputeHubClientError("semantic_compute_artifact_response_invalid")
        return reference

    def submit_result(self, result: Mapping[str, Any]) -> None:
        response = self._post("results", {"result": dict(result)}, 262_144)
        if response.get("accepted") is not True or response.get("task_id") != result.get("task_id"):
            raise SemanticComputeHubClientError("semantic_compute_result_rejected")

    def _post(self, operation: str, payload: Mapping[str, Any], response_limit: int) -> dict[str, Any]:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        request = urllib.request.Request(
            f"{self._hub_url}/v1/semantic-media/internal/compute/{operation}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Ananta-Worker-ID": self._worker_id,
                "X-Ananta-Worker-URL": self._worker_url,
                "User-Agent": "ananta-semantic-compute-worker/1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout, context=self._ssl_context) as response:
                raw = response.read(response_limit + 1)
        except urllib.error.HTTPError as exc:
            reason = "semantic_compute_hub_rejected"
            try:
                decoded = json.loads(exc.read(65_537).decode())
                reason = str(((decoded.get("error") or {}).get("code")) or reason)
            except Exception:
                pass
            raise SemanticComputeHubClientError(reason) from exc
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise SemanticComputeHubClientError("semantic_compute_hub_unavailable") from exc
        if len(raw) > response_limit:
            raise SemanticComputeHubClientError("semantic_compute_hub_response_too_large")
        try:
            decoded = json.loads(raw.decode())
            data = decoded.get("data")
        except (UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise SemanticComputeHubClientError("semantic_compute_hub_response_invalid") from exc
        if not isinstance(data, dict):
            raise SemanticComputeHubClientError("semantic_compute_hub_response_invalid")
        return data


class BoundedSemanticExecutor:
    """Small deterministic CPU implementation; it cannot access peers or queues."""

    def __init__(self, client: HttpSemanticComputeHubClient) -> None:
        self._client = client

    def execute(self, task: SemanticComputeWorkerTask, cancelled) -> WorkerArtifact:
        started = time.perf_counter()
        inputs = []
        total = 0
        for reference in task.input_refs:
            if cancelled():
                raise SemanticComputeWorkerError("execution_authority_lost")
            content, media_type = self._client.read_input(task, reference)
            total += len(content)
            if total > int(task.resource_budget["memory_bytes"]):
                raise SemanticComputeWorkerError("memory_budget_exceeded")
            inputs.append((reference, content, media_type))
        rows: list[dict[str, Any]] = []
        for reference, content, media_type in inputs:
            if cancelled():
                raise SemanticComputeWorkerError("execution_authority_lost")
            if task.task_type.startswith("visual_"):
                rows.append(self._visual(reference, content, media_type))
            elif task.task_type.startswith("speech_"):
                rows.append(self._speech(reference, content, media_type, cancelled))
            else:
                raise SemanticComputeWorkerError("task_type_unsupported")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if elapsed_ms > float(task.resource_budget["cpu_ms"]):
            raise SemanticComputeWorkerError("cpu_budget_exceeded")
        output = json.dumps(
            {
                "schema": "ananta.semantic-compute-artifact.v1",
                "task_id": task.task_id,
                "task_type": task.task_type,
                "results": rows,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        return WorkerArtifact(
            content=output,
            metrics={"input_bytes": float(total), "elapsed_ms": elapsed_ms, "input_count": float(len(rows))},
        )

    @staticmethod
    def _visual(reference: str, content: bytes, media_type: str) -> dict[str, Any]:
        try:
            from PIL import Image

            Image.MAX_IMAGE_PIXELS = 20_000_000
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                image.thumbnail((512, 512))
                rgb = image.convert("RGB")
                histogram = rgb.histogram()
                pixels = max(1, rgb.width * rgb.height)
                averages = [
                    round(sum(index * histogram[channel * 256 + index] for index in range(256)) / pixels, 3)
                    for channel in range(3)
                ]
                width, height = image.size
                mode = image.mode
        except Exception as exc:
            raise SemanticComputeWorkerError("visual_input_invalid") from exc
        return {
            "input_ref": reference,
            "media_type": media_type,
            "sha256": hashlib.sha256(content).hexdigest(),
            "width": width,
            "height": height,
            "mode": mode,
            "average_rgb": averages,
        }

    @staticmethod
    def _speech(reference: str, content: bytes, media_type: str, cancelled) -> dict[str, Any]:
        try:
            with wave.open(io.BytesIO(content), "rb") as wav:
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                sample_rate = wav.getframerate()
                frame_count = wav.getnframes()
                if channels not in {1, 2} or sample_width not in {1, 2, 4} or sample_rate < 8_000:
                    raise ValueError("format")
                remaining = min(frame_count, sample_rate * 300)
                square_sum = 0.0
                zero_crossings = 0
                sample_count = 0
                previous = 0
                format_by_width = {1: "b", 2: "h", 4: "i"}
                while remaining > 0:
                    if cancelled():
                        raise SemanticComputeWorkerError("execution_authority_lost")
                    chunk_frames = min(4_096, remaining)
                    raw = wav.readframes(chunk_frames)
                    values = struct.iter_unpack("<" + format_by_width[sample_width], raw)
                    for (sample,) in values:
                        square_sum += float(sample) * float(sample)
                        if (sample < 0 <= previous) or (sample >= 0 > previous):
                            zero_crossings += 1
                        previous = sample
                        sample_count += 1
                    remaining -= chunk_frames
        except SemanticComputeWorkerError:
            raise
        except Exception as exc:
            raise SemanticComputeWorkerError("speech_input_invalid") from exc
        peak = float((1 << (sample_width * 8 - 1)) - 1)
        rms = math.sqrt(square_sum / max(1, sample_count)) / max(1.0, peak)
        return {
            "input_ref": reference,
            "media_type": media_type,
            "sha256": hashlib.sha256(content).hexdigest(),
            "channels": channels,
            "sample_rate": sample_rate,
            "duration_ms": round(frame_count * 1000 / sample_rate, 3),
            "normalized_rms": round(rms, 8),
            "zero_crossing_rate": round(zero_crossings / max(1, sample_count), 8),
        }


class RegisteredSemanticComputeTaskHandler:
    """Task-registry adapter; no orchestration methods are exposed."""

    def __init__(
        self,
        runtime: SemanticComputeWorkerHandler,
        client: HttpSemanticComputeHubClient,
    ) -> None:
        self._runtime = runtime
        self._client = client

    def propose(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason": "semantic_compute_hub_delegation_required",
            "authoritative_source": "hub",
        }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        task = kwargs.get("task")
        context = task.get("worker_execution_context") if isinstance(task, Mapping) else None
        raw = context.get("semantic_compute") if isinstance(context, Mapping) else None
        if not isinstance(raw, Mapping):
            return {"status": "failed", "reason_code": "semantic_compute_context_missing", "exit_code": 1}
        try:
            result = self._runtime.handle(raw)
            self._client.submit_result(result)
        except Exception as exc:  # noqa: BLE001 - Worker boundary must fail closed
            reason = str(getattr(exc, "reason_code", "") or "semantic_compute_worker_failed")
            return {"status": "failed", "reason_code": reason, "output": reason, "exit_code": 1}
        return {
            "status": "completed",
            "reason_code": "semantic_compute_completed",
            "output": "Hub-delegated semantic compute completed.",
            "exit_code": 0,
            "semantic_compute_result": result,
            "artifacts": list(result.get("artifact_refs") or []),
        }


def build_semantic_compute_task_handler(
    client: HttpSemanticComputeHubClient | None = None,
) -> RegisteredSemanticComputeTaskHandler:
    resolved = client or HttpSemanticComputeHubClient.from_environment()
    runtime = SemanticComputeWorkerHandler(
        executor=BoundedSemanticExecutor(resolved),
        publisher=resolved,
        lease_guard=resolved,
    )
    return RegisteredSemanticComputeTaskHandler(runtime, resolved)


__all__ = [
    "BoundedSemanticExecutor",
    "HttpSemanticComputeHubClient",
    "RegisteredSemanticComputeTaskHandler",
    "SemanticComputeHubClientError",
    "SemanticComputeWorkerConfigurationError",
    "build_semantic_compute_task_handler",
]
