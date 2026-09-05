"""Real Ollama/GPU invocation stage for a Hub-bound Unsloth GGUF export."""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import requests

from agent.services.integration_registry_service import IntegrationRegistryService
from agent.services.model_invocation_service import ModelInvocationService
from agent.services.model_profile_loader import ModelProfile
from agent.services.runtime_handoff_invocation_service import (
    RuntimeHandoffInvocationService,
)
from agent.services.unsloth_evidence import ProvidedEvidenceRegistry
from agent.services.unsloth_runtime_endpoint_registry_service import (
    SqliteRuntimeEndpointRegistry,
)
from agent.services.unsloth_runtime_handoff_service import (
    RuntimeArtifact,
    RuntimeHandoffRequest,
    UnslothRuntimeHandoffService,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OllamaRuntimeProbeError(RuntimeError):
    """Bounded failure from the real provider runtime stage."""


class _ProbeAudit:
    def record(self, **_values: Any) -> None:
        return None


class _ProbeRuntimeTasks:
    """Test-gate adapter for the Hub-owned append-only endpoint registry."""

    def __init__(self, endpoints: SqliteRuntimeEndpointRegistry) -> None:
        self._endpoints = endpoints

    def submit(
        self,
        *,
        task_type: str,
        tenant_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> str:
        if task_type != "ml.runtime.artifact_handoff":
            raise OllamaRuntimeProbeError("ollama_probe_task_type_invalid")
        task_id = f"ollama-handoff-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]}"
        self._endpoints.apply_handoff(
            tenant_id=tenant_id,
            endpoint_id=str(payload["endpoint_id"]),
            expected_revision=int(payload["expected_endpoint_revision"]),
            task_id=task_id,
            idempotency_key=idempotency_key,
            manifest=payload,
        )
        return task_id


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_ollama_container_command(
    *,
    image: str,
    container_name: str,
    state_dir: Path,
    libraries: Mapping[str, Path],
    device_paths: Sequence[Path],
    nvidia_smi_path: Path,
) -> list[str]:
    if re.fullmatch(r"ananta-unsloth-ollama-[0-9a-f]{16}", container_name) is None:
        raise OllamaRuntimeProbeError("ollama_probe_container_name_invalid")
    command = [
        "docker",
        "run",
        "--detach",
        "--name",
        container_name,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "256",
        "--cpus",
        "4",
        "--memory",
        "12g",
        "--tmpfs",
        "/tmp:rw,exec,nosuid,nodev,size=1073741824",
        "--publish",
        "127.0.0.1::11434",
        "--volume",
        f"{state_dir.resolve(strict=True)}:/root/.ollama:rw",
        "--volume",
        f"{nvidia_smi_path.resolve(strict=True)}:/usr/bin/nvidia-smi:ro",
    ]
    for device in device_paths:
        command.extend(("--device", str(device.resolve(strict=True))))
    for name, path in libraries.items():
        command.extend(("--volume", f"{path.resolve(strict=True)}:/host-nvidia/{name}:ro"))
        if name == "libcuda.so.1":
            command.extend(("--volume", f"{path.resolve(strict=True)}:/host-nvidia/libcuda.so:ro"))
    command.extend(
        (
            "--env",
            "LD_LIBRARY_PATH=/host-nvidia",
            "--env",
            "NVIDIA_VISIBLE_DEVICES=0",
            "--env",
            "OLLAMA_NO_CLOUD=true",
            "--env",
            "OLLAMA_KEEP_ALIVE=5m",
            image,
        )
    )
    return command


def _run(command: Sequence[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        tuple(command),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _published_port(container_name: str) -> int:
    result = _run(("docker", "port", container_name, "11434/tcp"))
    value = result.stdout.strip().rsplit(":", 1)[-1]
    if result.returncode != 0 or not value.isdigit() or not 1 <= int(value) <= 65535:
        raise OllamaRuntimeProbeError("ollama_probe_published_port_invalid")
    return int(value)


def _wait_ready(base_url: str, *, timeout_seconds: float = 90.0) -> str:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{base_url}/api/version", timeout=2)
            if response.status_code == 200:
                version = str(response.json().get("version") or "").strip()
                if version:
                    return version
        except (requests.RequestException, ValueError):
            pass
        time.sleep(0.5)
    raise OllamaRuntimeProbeError("ollama_probe_readiness_timeout")


def _unload_model(base_url: str, model_name: str) -> None:
    response = requests.post(
        f"{base_url}/api/generate",
        json={"model": model_name, "keep_alive": 0, "stream": False},
        timeout=60,
    )
    if response.status_code != 200:
        raise OllamaRuntimeProbeError("ollama_probe_model_unload_failed")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = requests.get(f"{base_url}/api/ps", timeout=5)
        current.raise_for_status()
        if not any(
            item.get("model") == f"{model_name}:latest"
            for item in current.json().get("models") or []
        ):
            return
        time.sleep(0.25)
    raise OllamaRuntimeProbeError("ollama_probe_gpu_release_timeout")


def _cleanup_container(container_name: str) -> None:
    try:
        _run(
            ("docker", "exec", container_name, "chmod", "-R", "a+rwx", "/root/.ollama"),
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pass
    try:
        _run(("docker", "stop", "--timeout", "5", container_name), timeout=20)
    except subprocess.TimeoutExpired:
        try:
            _run(("docker", "kill", container_name), timeout=20)
        except subprocess.TimeoutExpired:
            pass
    try:
        removed = _run(("docker", "rm", "--force", container_name), timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise OllamaRuntimeProbeError("ollama_probe_container_cleanup_failed") from exc
    inspected = _run(("docker", "inspect", container_name), timeout=10)
    if removed.returncode not in {0, 1} or inspected.returncode == 0:
        raise OllamaRuntimeProbeError("ollama_probe_container_cleanup_failed")


def _baseline_manifest(
    *,
    descriptor: Mapping[str, Any],
    source_ids: Sequence[str],
    run_id: str,
    base_model_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "tenant_id": "ananta-local",
        "endpoint_id": descriptor["endpoint"]["endpoint_id"],
        "provider": descriptor["provider"]["provider_id"],
        "artifact": {
            "artifact_id": "unsloth-runtime-baseline",
            "artifact_sha256": base_model_sha256,
            "format": "gguf",
        },
        "provider_descriptor": descriptor["provider"],
        "endpoint_descriptor": descriptor["endpoint"],
        "api_capabilities": descriptor["api_capabilities"],
        "limits": descriptor["limits"],
        "source_ids": list(source_ids),
        "run_ids": [run_id],
        "job_id": "unsloth-runtime-baseline",
        "attempt_id": "unsloth-runtime-baseline-attempt",
        "fencing_token_digest": hashlib.sha256(b"baseline-fence").hexdigest(),
        "reason_sha256": hashlib.sha256(b"runtime baseline").hexdigest(),
        "expected_endpoint_revision": 0,
        "fallback": None,
    }


def run_ollama_runtime_probe(
    *,
    gguf_path: Path,
    base_model_sha256: str,
    ollama_image: str,
    ollama_image_id: str,
    assignment: Mapping[str, Any],
    state_dir: Path,
    endpoint_database: Path,
    libraries: Mapping[str, Path],
    device_paths: Sequence[Path],
    nvidia_smi_path: Path,
) -> dict[str, Any]:
    """Load, invoke and roll back one promoted GGUF without provider fallback."""
    source_ids = tuple(str(value) for value in assignment.get("source_ids") or ())
    run_id = str(assignment.get("run_id") or "")
    gguf_sha256 = sha256_file(gguf_path.resolve(strict=True))
    if not source_ids or not run_id.startswith("RUN_") or _SHA256.fullmatch(base_model_sha256) is None:
        raise OllamaRuntimeProbeError("ollama_probe_assignment_invalid")
    image_digest = ollama_image_id.removeprefix("sha256:")
    if _SHA256.fullmatch(image_digest) is None:
        raise OllamaRuntimeProbeError("ollama_probe_image_digest_invalid")
    suffix = hashlib.sha256(run_id.encode()).hexdigest()[:16]
    container_name = f"ananta-unsloth-ollama-{suffix}"
    model_name = f"ananta-unsloth-{suffix}"
    state_dir.resolve(strict=True)
    state_dir.chmod(0o777)
    command = build_ollama_container_command(
        image=ollama_image,
        container_name=container_name,
        state_dir=state_dir,
        libraries=libraries,
        device_paths=device_paths,
        nvidia_smi_path=nvidia_smi_path,
    )
    started = _run(command, timeout=120)
    if started.returncode != 0:
        raise OllamaRuntimeProbeError("ollama_probe_container_start_failed")
    try:
        base_url = f"http://localhost:{_published_port(container_name)}"
        version = _wait_ready(base_url)
        with gguf_path.open("rb") as handle:
            upload = requests.post(
                f"{base_url}/api/blobs/sha256:{gguf_sha256}",
                data=handle,
                timeout=300,
            )
        if upload.status_code != 201:
            raise OllamaRuntimeProbeError("ollama_probe_blob_upload_failed")
        created = requests.post(
            f"{base_url}/api/create",
            json={
                "model": model_name,
                "files": {gguf_path.name: f"sha256:{gguf_sha256}"},
                "stream": False,
            },
            timeout=300,
        )
        if created.status_code != 200 or created.json().get("status") != "success":
            raise OllamaRuntimeProbeError("ollama_probe_model_create_failed")

        descriptor = IntegrationRegistryService().normalize_runtime_endpoint_descriptor(
            provider_descriptor={
                "provider_id": "ollama",
                "provider_type": "local-openai-compatible",
                "model_id": model_name,
                "provider_revision": f"sha256:{image_digest}",
                "capabilities": {
                    "openai_chat": True,
                    "openai_responses": False,
                    "anthropic_messages": False,
                    "streaming": False,
                    "tools": False,
                    "structured_output": False,
                },
                "limits": {
                    "timeout_seconds": 180,
                    "context_tokens": 4096,
                    "max_output_tokens": 8,
                    "stream_idle_timeout_seconds": 30,
                },
            },
            endpoint_descriptor={
                "endpoint_id": f"unsloth-ollama-{suffix}",
                "display_name": "Hub-bound Unsloth Ollama GPU endpoint",
                "routing_key": f"ollama-{suffix}",
            },
        )
        endpoints = SqliteRuntimeEndpointRegistry(endpoint_database)
        endpoints.apply_handoff(
            tenant_id="ananta-local",
            endpoint_id=descriptor["endpoint"]["endpoint_id"],
            expected_revision=0,
            task_id=f"ollama-baseline-{suffix}",
            idempotency_key=f"ollama-baseline:{run_id}",
            manifest=_baseline_manifest(
                descriptor=descriptor,
                source_ids=source_ids,
                run_id=run_id,
                base_model_sha256=base_model_sha256,
            ),
        )
        handoff = UnslothRuntimeHandoffService(
            tasks=_ProbeRuntimeTasks(endpoints),
            audit=_ProbeAudit(),
            evidence=ProvidedEvidenceRegistry(source_ids=source_ids, run_ids=(run_id,)),
        )
        plan = handoff.plan(
            RuntimeHandoffRequest(
                tenant_id="ananta-local",
                endpoint_id=descriptor["endpoint"]["endpoint_id"],
                provider="ollama",
                artifact=RuntimeArtifact(
                    artifact_id=f"unsloth-gguf-{gguf_sha256[:24]}",
                    tenant_id="ananta-local",
                    artifact_sha256=gguf_sha256,
                    registry_state="promoted",
                    verification_state="verified",
                    format="gguf",
                ),
                source_ids=source_ids,
                run_ids=(run_id,),
                expected_endpoint_revision=1,
                provider_descriptor=descriptor["provider"],
                endpoint_descriptor=descriptor["endpoint"],
                api_capabilities=descriptor["api_capabilities"],
                limits=descriptor["limits"],
                promotion_id=f"hub-gate-promotion-{gguf_sha256[:24]}",
                adapter_id="nvidia-live-smoke-adapter",
                adapter_sha256=gguf_sha256,
                base_model_id="local/nvidia-smoke-model",
                base_model_sha256=base_model_sha256,
                job_id="nvidia-live-smoke",
                attempt_id="nvidia-live-smoke-attempt-1",
                fencing_token_digest=hashlib.sha256(b"1").hexdigest(),
                reason_sha256=hashlib.sha256(b"real ollama runtime handoff").hexdigest(),
            )
        )
        handoff_task = handoff.submit(
            plan,
            confirmation_digest=plan.confirmation_digest,
            idempotency_key=f"real-ollama-handoff:{run_id}:{gguf_sha256}",
        )
        resolved = ModelInvocationService.resolve_runtime_handoff_endpoint(
            tenant_id="ananta-local",
            endpoint_id=descriptor["endpoint"]["endpoint_id"],
            required_capability="openai_chat",
            expected_endpoint_revision=2,
            endpoint_registry=endpoints,
        )
        profile = ModelProfile(
            profile_id=f"unsloth-ollama-{suffix}",
            provider_id="ollama",
            model=model_name,
            local=True,
            cloud=False,
            base_url=f"{base_url}/api/generate",
            context_tokens=int(resolved["limits"]["context_tokens"]),
            max_output_tokens=int(resolved["limits"]["max_output_tokens"]),
            timeout_seconds=int(resolved["limits"]["timeout_seconds"]),
        )
        invocation = RuntimeHandoffInvocationService().invoke_result(
            f"Return one short token for run {run_id}.",
            endpoint=resolved,
            profile=profile,
        )
        message = str(invocation["choices"][0]["message"].get("content") or "").strip()
        middleware = dict(invocation.get("metadata", {}).get("provider_middleware") or {})
        calls = list(invocation.get("metadata", {}).get("llm_call_profile") or [])
        active = requests.get(f"{base_url}/api/ps", timeout=10)
        active.raise_for_status()
        loaded = [item for item in active.json().get("models") or [] if item.get("model") == f"{model_name}:latest"]
        if (
            not message
            or len(calls) != 1
            or calls[0].get("success") is not True
            or calls[0].get("provider") != "ollama"
            or middleware.get("cache_hit") is not False
            or len(loaded) != 1
            or int(loaded[0].get("size_vram") or 0) <= 0
            or resolved.get("fallback") is not None
        ):
            raise OllamaRuntimeProbeError("ollama_probe_invocation_attestation_failed")
        rollback = endpoints.rollback(
            tenant_id="ananta-local",
            endpoint_id=descriptor["endpoint"]["endpoint_id"],
            expected_revision=2,
            reason_sha256=hashlib.sha256(b"real ollama runtime rollback").hexdigest(),
            actor_id="system:unsloth-gpu-release-gate",
        )
        _unload_model(base_url, model_name)
        return {
            "status": "passed",
            "provider": "ollama",
            "provider_version": version,
            "provider_image_id": ollama_image_id,
            "model": model_name,
            "model_manifest_digest": loaded[0].get("digest"),
            "gguf_sha256": gguf_sha256,
            "gguf_size_bytes": gguf_path.stat().st_size,
            "gpu_size_vram_bytes": int(loaded[0]["size_vram"]),
            "response_sha256": hashlib.sha256(message.encode()).hexdigest(),
            "prompt_tokens": calls[0].get("prompt_tokens"),
            "completion_tokens": calls[0].get("completion_tokens"),
            "endpoint_id": resolved["endpoint_id"],
            "handoff_revision": resolved["endpoint_revision"],
            "handoff_task_id": handoff_task,
            "handoff_plan_sha256": plan.confirmation_digest,
            "rollback_revision": rollback.revision,
            "rollback_restored_from_revision": rollback.restored_from_revision,
            "implicit_fallback": False,
            "cache_hit": False,
            "gpu_resources_released": True,
        }
    finally:
        _cleanup_container(container_name)


__all__ = [
    "OllamaRuntimeProbeError",
    "build_ollama_container_command",
    "run_ollama_runtime_probe",
    "sha256_file",
]
