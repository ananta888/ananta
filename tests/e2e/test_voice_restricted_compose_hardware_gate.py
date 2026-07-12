"""Opt-in hardware gate for real Voice and restricted-inference containers.

This module is intentionally skipped during normal CI. The release-gate runner
refuses to invoke it until every required input is explicit and rejects all
skips, so absent hardware, models, or licensed audio can never be reported as
successful evidence. Resource-exhaustion coverage uses manifest-declared
admission budgets and never attempts to exhaust the host.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker" / "compose-next" / "compose.voice-restricted.yml"
HARDWARE_PROFILES = ROOT / "config" / "release-gates" / "voice-restricted-hardware-profiles.v1.json"
ALLOWED_BACKENDS = frozenset({"vosk", "whisper_cpp", "faster_whisper", "voxtral"})
WORD_TIMESTAMP_BACKENDS = frozenset({"vosk", "faster_whisper"})
CLASSIC_CONTEXT_BACKENDS = frozenset({"whisper_cpp"})
CONTROLLED_ADMISSION_REASONS = frozenset({"ram_budget_exhausted", "vram_budget_exhausted"})
SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
MANIFEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,191}$")
COMPOSE_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

pytestmark = [pytest.mark.integration, pytest.mark.hardware]


@dataclass(frozen=True)
class RunningHardwareStack:
    project: str
    compose_profile: str
    hub_url: str
    profile_id: str
    access_token: str
    audio_path: Path
    audio_digest: str
    language: str
    primary_backend: str
    secondary_backend: str
    choice_manifest_id: str
    admission_denied_manifest_id: str
    admission_reason: str
    restricted_service: str
    voice_service: str


def _required_environment(name: str) -> str:
    value = str(os.getenv(name, "")).strip()
    if not value:
        pytest.skip(f"explicit hardware evidence input is missing: {name}")
    return value


def _hardware_profile(profile_id: str) -> dict[str, Any]:
    payload = json.loads(HARDWARE_PROFILES.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != (
        "ananta.voice-restricted-hardware-profiles.v1"
    ):
        pytest.fail("hardware profile contract is invalid", pytrace=False)
    matches = [
        dict(item)
        for item in payload.get("profiles", [])
        if isinstance(item, dict) and item.get("profile_id") == profile_id
    ]
    if len(matches) != 1:
        pytest.fail(f"unsupported hardware profile: {profile_id}", pytrace=False)
    profile = matches[0]
    compose_profile = profile.get("compose_profile")
    voice_backends = profile.get("voice_backends")
    if not isinstance(compose_profile, str) or not isinstance(voice_backends, list):
        pytest.fail("hardware profile lacks its Compose/backend contract", pytrace=False)
    if not voice_backends or any(item not in ALLOWED_BACKENDS for item in voice_backends):
        pytest.fail("hardware profile contains an invalid Voice backend allowlist", pytrace=False)
    return profile


def _required_non_negative_integer(name: str) -> int:
    raw = _required_environment(name)
    try:
        value = int(raw)
    except ValueError:
        pytest.fail(f"{name} must be a non-negative integer", pytrace=False)
    if value < 0:
        pytest.fail(f"{name} must be a non-negative integer", pytrace=False)
    return value


def _required_unprivileged_port(name: str) -> int:
    raw = _required_environment(name)
    try:
        value = int(raw)
    except ValueError:
        pytest.fail(f"{name} must be an integer TCP port", pytrace=False)
    if not 1024 <= value <= 65535:
        pytest.fail(f"{name} must be an unprivileged TCP port", pytrace=False)
    return value


def _require_available_loopback_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            pytest.fail(f"hardware Hub port {port} is already in use", pytrace=False)


def _compose_command(project: str, profile: str, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        project,
        "-f",
        str(COMPOSE),
        "--profile",
        profile,
        *arguments,
    ]


def _run_compose(
    stack: RunningHardwareStack,
    *arguments: str,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _compose_command(stack.project, stack.compose_profile, *arguments),
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _api_data(response: requests.Response) -> dict[str, Any]:
    envelope = response.json()
    assert isinstance(envelope, dict)
    data = envelope.get("data")
    return dict(data) if isinstance(data, dict) else envelope


def _api_error_code(response: requests.Response) -> str:
    payload = _api_data(response)
    error = payload.get("error")
    return str(error.get("code") or "") if isinstance(error, dict) else ""


def _auth_headers(
    stack: RunningHardwareStack,
    *,
    idempotency_key: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {stack.access_token}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if run_id:
        headers["X-Run-ID"] = run_id
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers


def _hardware_manifest(root: Path, manifest_id: str) -> dict[str, Any]:
    manifest_root = root / "manifests"
    path = manifest_root / f"{manifest_id}.json"
    if path.is_symlink():
        pytest.fail("admission-denied manifest may not be a symlink", pytrace=False)
    try:
        resolved_root = manifest_root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        resolved_path.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        pytest.fail("admission-denied manifest is not a regular file below manifests/", pytrace=False)
    if not resolved_path.is_file():
        pytest.fail("admission-denied manifest is not a regular file", pytrace=False)
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        pytest.fail("admission-denied manifest is not valid UTF-8 JSON", pytrace=False)
    if not isinstance(payload, dict) or payload.get("manifest_id") != manifest_id:
        pytest.fail("admission-denied manifest ID does not match its filename", pytrace=False)
    return dict(payload)


def _validate_controlled_admission_input(
    *,
    restricted_root: Path,
    manifest_id: str,
    reason: str,
    max_ram_bytes: int,
    max_vram_bytes: int,
) -> None:
    payload = _hardware_manifest(restricted_root, manifest_id)
    field, budget = {
        "ram_budget_exhausted": ("ram_bytes", max_ram_bytes),
        "vram_budget_exhausted": ("vram_bytes", max_vram_bytes),
    }[reason]
    declared = payload.get(field)
    if isinstance(declared, bool) or not isinstance(declared, int) or declared <= budget:
        pytest.fail(
            f"{manifest_id} must declare {field} greater than the explicit worker budget; "
            "the gate refuses an allocation-based OOM probe",
            pytrace=False,
        )


@pytest.fixture(scope="module")
def running_stack() -> Iterator[RunningHardwareStack]:
    if _required_environment("ANANTA_RUN_VOICE_RESTRICTED_HARDWARE") != "1":
        pytest.skip("hardware execution requires ANANTA_RUN_VOICE_RESTRICTED_HARDWARE=1")
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is unavailable")

    profile_id = _required_environment("ANANTA_VOICE_RESTRICTED_PROFILE")
    hardware_profile = _hardware_profile(profile_id)
    resolved_inputs: dict[str, Path] = {}
    for path_variable in ("VOICE_MODEL_DIR", "RESTRICTED_INFERENCE_MODEL_DIR", "ANANTA_VOICE_E2E_AUDIO"):
        configured_path = Path(_required_environment(path_variable)).expanduser()
        if configured_path.is_symlink():
            pytest.fail(f"hardware input may not be a symlink: {path_variable}")
        resolved_inputs[path_variable] = configured_path.resolve(strict=True)
    assert resolved_inputs["VOICE_MODEL_DIR"].is_dir()
    assert resolved_inputs["RESTRICTED_INFERENCE_MODEL_DIR"].is_dir()
    assert resolved_inputs["ANANTA_VOICE_E2E_AUDIO"].is_file()

    expected_audio_digest = _required_environment("ANANTA_VOICE_E2E_AUDIO_SHA256").removeprefix("sha256:")
    if not SHA256_RE.fullmatch(expected_audio_digest):
        pytest.fail("ANANTA_VOICE_E2E_AUDIO_SHA256 must be a lowercase SHA-256 digest")
    actual_audio_digest = _file_sha256(resolved_inputs["ANANTA_VOICE_E2E_AUDIO"])
    if actual_audio_digest != expected_audio_digest:
        pytest.fail("the hardware audio fixture does not match its declared digest")

    primary_backend = _required_environment("ANANTA_VOICE_E2E_PRIMARY_BACKEND")
    secondary_backend = _required_environment("ANANTA_VOICE_E2E_SECONDARY_BACKEND")
    if {primary_backend, secondary_backend} - ALLOWED_BACKENDS or primary_backend == secondary_backend:
        pytest.fail("hardware primary and secondary backends must be distinct supported real backends")
    profile_backends = frozenset(str(item) for item in hardware_profile["voice_backends"])
    if {primary_backend, secondary_backend} - profile_backends:
        pytest.fail("hardware backends must be enabled by the selected Compose profile")
    if not {primary_backend, secondary_backend} & WORD_TIMESTAMP_BACKENDS:
        pytest.fail("at least one hardware backend must emit word-level timestamp provenance")
    if secondary_backend not in CLASSIC_CONTEXT_BACKENDS:
        pytest.fail("the classic_then_correct secondary backend must accept a transcript reference")
    prompt_max_chars = _required_non_negative_integer("VOICE_WHISPER_CPP_PROMPT_MAX_CHARS")
    if not 1 <= prompt_max_chars <= 8_000:
        pytest.fail("VOICE_WHISPER_CPP_PROMPT_MAX_CHARS must be between 1 and 8000")

    choice_manifest_id = _required_environment("ANANTA_RESTRICTED_INFERENCE_MANIFEST_SCORE_CHOICES")
    admission_manifest_id = _required_environment(
        "ANANTA_RESTRICTED_INFERENCE_ADMISSION_DENIED_MANIFEST_ID"
    )
    if not MANIFEST_ID_RE.fullmatch(choice_manifest_id) or not MANIFEST_ID_RE.fullmatch(admission_manifest_id):
        pytest.fail("hardware restricted-inference manifest IDs are invalid")
    if choice_manifest_id == admission_manifest_id:
        pytest.fail("choice and admission-denied evidence must use distinct pinned manifests")
    admission_reason = _required_environment("ANANTA_RESTRICTED_INFERENCE_ADMISSION_REASON")
    if admission_reason not in CONTROLLED_ADMISSION_REASONS:
        pytest.fail(
            "ANANTA_RESTRICTED_INFERENCE_ADMISSION_REASON must select a deterministic RAM/VRAM budget denial"
        )
    max_ram_bytes = _required_non_negative_integer("ANANTA_RESTRICTED_INFERENCE_MAX_RAM_BYTES")
    max_vram_bytes = _required_non_negative_integer("ANANTA_RESTRICTED_INFERENCE_MAX_VRAM_BYTES")
    _validate_controlled_admission_input(
        restricted_root=resolved_inputs["RESTRICTED_INFERENCE_MODEL_DIR"],
        manifest_id=admission_manifest_id,
        reason=admission_reason,
        max_ram_bytes=max_ram_bytes,
        max_vram_bytes=max_vram_bytes,
    )

    secret_names = (
        "INITIAL_ADMIN_PASSWORD",
        "VOICE_INTERNAL_SERVICE_TOKEN",
        "VOICE_PERSONALIZATION_ENCRYPTION_KEY",
        "RESTRICTED_INFERENCE_INTERNAL_TOKEN",
    )
    secrets = {name: _required_environment(name) for name in secret_names}
    service_secrets = {
        secrets["VOICE_INTERNAL_SERVICE_TOKEN"],
        secrets["VOICE_PERSONALIZATION_ENCRYPTION_KEY"],
        secrets["RESTRICTED_INFERENCE_INTERNAL_TOKEN"],
    }
    if len(service_secrets) != 3:
        pytest.fail("Voice, restricted-inference and personalization secrets must be distinct")
    if min(
        len(secrets["VOICE_INTERNAL_SERVICE_TOKEN"]),
        len(secrets["RESTRICTED_INFERENCE_INTERNAL_TOKEN"]),
    ) < 24:
        pytest.fail("internal runtime service tokens must contain at least 24 characters")
    try:
        from cryptography.fernet import Fernet

        Fernet(secrets["VOICE_PERSONALIZATION_ENCRYPTION_KEY"].encode("ascii"))
    except (ImportError, UnicodeError, ValueError):
        pytest.fail("VOICE_PERSONALIZATION_ENCRYPTION_KEY must be a valid Fernet key", pytrace=False)

    compose_profile = str(hardware_profile["compose_profile"])
    suffix = "cpu" if compose_profile.endswith("cpu") else "nvidia"
    project = str(os.getenv("ANANTA_VOICE_RESTRICTED_PROJECT", "")).strip()
    project = project or f"ananta-vprf-hardware-{uuid.uuid4().hex[:12]}"
    if not COMPOSE_PROJECT_RE.fullmatch(project):
        pytest.fail("ANANTA_VOICE_RESTRICTED_PROJECT is not a safe Compose project name")
    hub_port = _required_unprivileged_port("HUB_PORT")
    _require_available_loopback_port(hub_port)
    preexisting = subprocess.run(
        _compose_command(project, compose_profile, "ps", "-a", "-q"),
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if preexisting.returncode:
        pytest.fail(f"compose project ownership check failed: {preexisting.stderr[-2000:]}")
    if preexisting.stdout.strip():
        pytest.fail("hardware gate refuses to adopt an existing Compose project")

    arguments = ["up", "-d", "--wait", "--wait-timeout", "300"]
    if str(os.getenv("ANANTA_VOICE_RESTRICTED_BUILD", "0")) != "1":
        arguments.insert(1, "--no-build")
    fixture_yielded = False
    try:
        hub_url = f"http://127.0.0.1:{hub_port}"
        completed = subprocess.run(
            _compose_command(project, compose_profile, *arguments),
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
            timeout=600,
        )
        if completed.returncode:
            pytest.fail(f"compose stack failed to become ready: {completed.stderr[-2000:]}")
        login = requests.post(
            f"{hub_url}/login",
            json={
                "username": str(os.getenv("INITIAL_ADMIN_USER", "admin")),
                "password": secrets["INITIAL_ADMIN_PASSWORD"],
            },
            timeout=30,
        )
        login.raise_for_status()
        token = str((_api_data(login)).get("access_token") or "")
        assert token
        stack = RunningHardwareStack(
            project=project,
            compose_profile=compose_profile,
            hub_url=hub_url,
            profile_id=profile_id,
            access_token=token,
            audio_path=resolved_inputs["ANANTA_VOICE_E2E_AUDIO"],
            audio_digest=actual_audio_digest,
            language=str(os.getenv("ANANTA_VOICE_E2E_LANGUAGE", "de")).strip() or "de",
            primary_backend=primary_backend,
            secondary_backend=secondary_backend,
            choice_manifest_id=choice_manifest_id,
            admission_denied_manifest_id=admission_manifest_id,
            admission_reason=admission_reason,
            restricted_service=f"restricted-inference-{suffix}",
            voice_service=f"voice-runtime-{suffix}",
        )
        fixture_yielded = True
        yield stack
    finally:
        keep_completed_stack = (
            fixture_yielded and str(os.getenv("ANANTA_VOICE_RESTRICTED_KEEP_STACK", "0")) == "1"
        )
        if not keep_completed_stack:
            subprocess.run(
                _compose_command(project, compose_profile, "down", "--remove-orphans", "--volumes"),
                cwd=ROOT,
                check=False,
                timeout=120,
            )


def _service_container(stack: RunningHardwareStack, service: str) -> str:
    completed = _run_compose(stack, "ps", "-q", service, timeout=30)
    assert completed.returncode == 0, completed.stderr
    container_id = completed.stdout.strip()
    assert container_id, service
    return container_id


def _configure_profile(
    stack: RunningHardwareStack,
    *,
    strategy: str,
    restricted_choice: bool,
) -> str:
    profile_id = f"vprf-hardware-{strategy}-{uuid.uuid4().hex[:16]}"
    feature_flags = {"voice_fusion": strategy == "parallel_compare"}
    correction_policy = "none"
    if restricted_choice:
        correction_policy = "restricted_choice"
        feature_flags["restricted_worker"] = True
    response = requests.put(
        f"{stack.hub_url}/v1/voice/configuration",
        headers=_auth_headers(stack, idempotency_key=f"config-{uuid.uuid4().hex}"),
        json={
            "scope": "profile",
            "scope_id": profile_id,
            "delta": {
                "recognition_strategy": strategy,
                "routing_strategy": "fixed",
                "correction_policy": correction_policy,
                "primary_backend": stack.primary_backend,
                "secondary_backends": [stack.secondary_backend],
                "max_parallel_backends": 2,
                "candidate_deadline_sec": 180.0,
                "feature_flags": feature_flags,
            },
        },
        timeout=30,
    )
    response.raise_for_status()
    configuration = _api_data(response).get("configuration")
    assert isinstance(configuration, dict)
    assert configuration.get("scope_id") == profile_id
    assert (configuration.get("delta") or {}).get("recognition_strategy") == strategy
    return profile_id


def _transcribe(
    stack: RunningHardwareStack,
    *,
    profile_id: str | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    headers = _auth_headers(
        stack,
        idempotency_key=f"voice-hardware-{uuid.uuid4().hex}",
        run_id=run_id,
    )
    data = {"language": stack.language}
    if profile_id:
        data["profile_id"] = profile_id
    with stack.audio_path.open("rb") as handle:
        response = requests.post(
            f"{stack.hub_url}/v1/voice/transcribe",
            headers=headers,
            files={"file": (stack.audio_path.name, handle, "audio/wav")},
            data=data,
            timeout=240,
        )
    response.raise_for_status()
    payload = _api_data(response)
    assert str(payload.get("result_ref") or "").startswith("voice-result-")
    assert str(payload.get("task_id") or "").startswith("voice-transcription-")
    assert str(payload.get("audit_id") or "").startswith("audit-voice-")
    assert payload.get("idempotent_replay") is False
    assert json.dumps(payload, allow_nan=False)
    return payload


def _successful_candidates(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates = payload.get("candidates")
    assert isinstance(candidates, list) and candidates
    succeeded = [dict(candidate) for candidate in candidates if candidate.get("status") == "succeeded"]
    assert succeeded
    return succeeded


def _assert_real_candidate_provenance(
    payload: Mapping[str, Any],
    *,
    expected_backends: set[str] | None = None,
) -> list[dict[str, Any]]:
    assert payload.get("provenance_valid") is True
    succeeded = _successful_candidates(payload)
    if expected_backends is not None:
        assert {str(candidate.get("backend") or "") for candidate in succeeded} == expected_backends
    word_count = 0
    source_audio_ids: set[str] = set()
    for candidate in succeeded:
        assert candidate.get("candidate_id")
        assert candidate.get("model_revision")
        assert SHA256_RE.fullmatch(str(candidate.get("manifest_digest") or ""))
        assert candidate.get("execution_location") == "voice-runtime"
        assert candidate.get("synthetic") is False
        source_audio_id = str(candidate.get("source_audio_digest") or "")
        assert source_audio_id.startswith("audio-lineage:")
        source_audio_ids.add(source_audio_id)
        provenance = candidate.get("provenance")
        assert isinstance(provenance, dict)
        assert provenance.get("raw_backend")
        words = candidate.get("words")
        assert isinstance(words, list)
        for word in words:
            assert isinstance(word, dict)
            assert word.get("candidate_id") == candidate.get("candidate_id")
            assert isinstance(word.get("start_ms"), int)
            assert isinstance(word.get("end_ms"), int)
            assert word["end_ms"] >= word["start_ms"] >= 0
            assert str(word.get("text") or "").strip()
            word_count += 1
    assert word_count > 0
    assert len(source_audio_ids) == 1
    return succeeded


def _assert_stage(payload: Mapping[str, Any], *, stage: str, **expected: Any) -> None:
    stages = payload.get("stages")
    assert isinstance(stages, list)
    matches = [item for item in stages if isinstance(item, dict) and item.get("stage") == stage]
    assert matches, stage
    assert any(all(item.get(key) == value for key, value in expected.items()) for item in matches)


def _restricted_tracking_task_id(*, parent_task_id: str, request_id: str, run_id: str) -> str:
    correlation = f"{parent_task_id}\0{request_id}\0{run_id}".encode()
    return f"restricted-inference-{hashlib.sha256(correlation).hexdigest()[:32]}"


def _restricted_choice_task(
    stack: RunningHardwareStack,
    *,
    voice_payload: Mapping[str, Any],
    run_id: str,
) -> dict[str, Any]:
    tracking_id = _restricted_tracking_task_id(
        parent_task_id=str(voice_payload.get("task_id") or ""),
        request_id=str(voice_payload.get("audit_id") or ""),
        run_id=run_id,
    )
    response = requests.get(
        f"{stack.hub_url}/tasks/{tracking_id}",
        headers=_auth_headers(stack),
        timeout=30,
    )
    response.raise_for_status()
    task = _api_data(response)
    assert task.get("id") == tracking_id
    return task


def _assert_restricted_choice_task(
    stack: RunningHardwareStack,
    *,
    voice_payload: Mapping[str, Any],
    profile_id: str,
    run_id: str,
) -> dict[str, Any]:
    parent_task_id = str(voice_payload.get("task_id") or "")
    request_id = str(voice_payload.get("audit_id") or "")
    _assert_voice_task(stack, voice_payload=voice_payload, profile_id=profile_id)
    task = _restricted_choice_task(stack, voice_payload=voice_payload, run_id=run_id)
    assert task.get("task_kind") == "restricted_inference"
    assert task.get("parent_task_id") == parent_task_id
    assert task.get("status") == "completed"
    assert task.get("required_capabilities") == ["restricted_inference", "score_choices"]
    restricted = ((task.get("worker_execution_context") or {}).get("restricted_inference") or {})
    assert restricted.get("operation") == "score_choices"
    assert restricted.get("no_generation") is True
    assert restricted.get("request_id") == request_id
    assert restricted.get("run_id") == run_id
    assert restricted.get("manifest_id") == stack.choice_manifest_id
    assert SHA256_RE.fullmatch(str(restricted.get("request_digest") or ""))
    verification = ((task.get("verification_status") or {}).get("restricted_inference") or {})
    assert verification == {
        "status": "succeeded",
        "no_generation": True,
        "request_id": request_id,
        "run_id": run_id,
    }
    return task


def _assert_failed_restricted_choice_task(
    stack: RunningHardwareStack,
    *,
    voice_payload: Mapping[str, Any],
    profile_id: str,
    run_id: str,
) -> dict[str, Any]:
    parent_task_id = str(voice_payload.get("task_id") or "")
    request_id = str(voice_payload.get("audit_id") or "")
    _assert_voice_task(stack, voice_payload=voice_payload, profile_id=profile_id)
    task = _restricted_choice_task(stack, voice_payload=voice_payload, run_id=run_id)
    assert task.get("task_kind") == "restricted_inference"
    assert task.get("parent_task_id") == parent_task_id
    assert task.get("status") == "failed"
    assert task.get("status_reason_code") == "restricted_inference_failed"
    assert task.get("required_capabilities") == ["restricted_inference", "score_choices"]
    restricted = ((task.get("worker_execution_context") or {}).get("restricted_inference") or {})
    assert restricted.get("operation") == "score_choices"
    assert restricted.get("no_generation") is True
    assert restricted.get("request_id") == request_id
    assert restricted.get("run_id") == run_id
    assert restricted.get("manifest_id") == stack.choice_manifest_id
    assert SHA256_RE.fullmatch(str(restricted.get("request_digest") or ""))
    assert "restricted_inference" not in (task.get("verification_status") or {})
    return task


def _assert_voice_task(
    stack: RunningHardwareStack,
    *,
    voice_payload: Mapping[str, Any],
    profile_id: str,
) -> dict[str, Any]:
    task_id = str(voice_payload.get("task_id") or "")
    response = requests.get(
        f"{stack.hub_url}/tasks/{task_id}",
        headers=_auth_headers(stack),
        timeout=30,
    )
    response.raise_for_status()
    task = _api_data(response)
    assert task.get("id") == task_id
    assert task.get("task_kind") == "voice_transcription"
    assert task.get("status") == "completed"
    context = ((task.get("worker_execution_context") or {}).get("voice_transcription") or {})
    assert context.get("request_id") == voice_payload.get("audit_id")
    assert context.get("request_digest") == stack.audio_digest
    assert context.get("profile_id") == profile_id
    assert SHA256_RE.fullmatch(str(context.get("configuration_digest") or ""))
    verification = ((task.get("verification_status") or {}).get("voice_transcription") or {})
    assert verification.get("status") == "verified"
    assert verification.get("result_ref") == voice_payload.get("result_ref")
    return task


def _restricted_status(stack: RunningHardwareStack) -> requests.Response:
    return requests.get(
        f"{stack.hub_url}/v1/voice/restricted-inference/status",
        headers=_auth_headers(stack),
        timeout=15,
    )


def _restart_restricted_worker(stack: RunningHardwareStack) -> None:
    restarted = _run_compose(
        stack,
        "up",
        "-d",
        "--no-deps",
        "--no-build",
        "--wait",
        "--wait-timeout",
        "180",
        stack.restricted_service,
        timeout=240,
    )
    assert restarted.returncode == 0, restarted.stderr


def test_runtime_containers_have_no_external_egress(running_stack: RunningHardwareStack) -> None:
    for service in (running_stack.voice_service, running_stack.restricted_service):
        container_id = _service_container(running_stack, service)
        probe = subprocess.run(
            [
                "docker",
                "exec",
                container_id,
                "python",
                "-c",
                "import urllib.request;urllib.request.urlopen('https://example.com',timeout=3).read(1)",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert probe.returncode != 0, f"{service} unexpectedly reached the public network"


def test_hub_can_reach_ready_runtimes_without_peer_network(running_stack: RunningHardwareStack) -> None:
    hub = _service_container(running_stack, "ai-agent-hub")
    probe_script = """
import json, urllib.request
for endpoint in ('http://voice-runtime:8090/health','http://restricted-inference-worker:8091/health'):
    payload=json.load(urllib.request.urlopen(endpoint, timeout=5))
    assert payload.get('status') in {'ready','ok'}, payload
"""
    completed = subprocess.run(
        ["docker", "exec", hub, "python", "-c", probe_script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


def test_controlled_resource_budget_denial_does_not_attempt_host_oom(
    running_stack: RunningHardwareStack,
) -> None:
    response = requests.post(
        (
            f"{running_stack.hub_url}/v1/voice/restricted-inference/models/"
            f"{running_stack.admission_denied_manifest_id}/load"
        ),
        headers=_auth_headers(
            running_stack,
            idempotency_key=f"admission-denied-{uuid.uuid4().hex}",
            request_id=f"vprf-admission-{uuid.uuid4().hex}",
        ),
        json={"deadline_seconds": 120.0},
        timeout=150,
    )
    assert response.status_code == 409
    assert _api_error_code(response) == running_stack.admission_reason

    status_response = _restricted_status(running_stack)
    status_response.raise_for_status()
    status = _api_data(status_response).get("restricted_inference")
    assert isinstance(status, dict) and status.get("status") == "ready"
    records = [
        item
        for item in status.get("models", [])
        if isinstance(item, dict) and item.get("manifest_id") == running_stack.admission_denied_manifest_id
    ]
    assert records
    assert records[-1].get("state") == "failed"
    assert records[-1].get("failure_code") == running_stack.admission_reason


def test_parallel_fusion_profile_executes_real_choice_with_hub_task_correlation(
    running_stack: RunningHardwareStack,
) -> None:
    # ``parallel_compare`` is the canonical Hub wire name for the parallel
    # fusion execution path; the runtime decision trace must prove parallelism.
    profile_id = _configure_profile(
        running_stack,
        strategy="parallel_compare",
        restricted_choice=True,
    )
    run_id = f"vprf-parallel-{uuid.uuid4().hex}"
    payload = _transcribe(running_stack, profile_id=profile_id, run_id=run_id)
    _assert_real_candidate_provenance(
        payload,
        expected_backends={running_stack.primary_backend, running_stack.secondary_backend},
    )
    assert payload.get("fusion_strategy") == "parallel_compare"
    trace = payload.get("decision_trace")
    assert isinstance(trace, dict) and trace.get("execution") == "parallel"
    _assert_stage(payload, stage="candidate_execution", mode="parallel", count=2)
    _assert_stage(payload, stage="fusion", strategy="parallel_compare")
    _assert_restricted_choice_task(
        running_stack,
        voice_payload=payload,
        profile_id=profile_id,
        run_id=run_id,
    )
    restricted_trace = trace.get("restricted_choice")
    if isinstance(restricted_trace, dict):
        assert restricted_trace.get("no_generation") is True
        assert SHA256_RE.fullmatch(str(restricted_trace.get("manifest_digest") or ""))


def test_classic_then_correct_profile_executes_real_sequential_lineage(
    running_stack: RunningHardwareStack,
) -> None:
    profile_id = _configure_profile(
        running_stack,
        strategy="classic_then_correct",
        restricted_choice=False,
    )
    payload = _transcribe(running_stack, profile_id=profile_id)
    _assert_voice_task(running_stack, voice_payload=payload, profile_id=profile_id)
    succeeded = _assert_real_candidate_provenance(
        payload,
        expected_backends={running_stack.primary_backend, running_stack.secondary_backend},
    )
    assert payload.get("fusion_strategy") == "classic_then_correct"
    trace = payload.get("decision_trace")
    assert isinstance(trace, dict)
    assert trace.get("execution") == "sequential"
    assert trace.get("corrector_status") == "succeeded"
    assert (trace.get("lineage_validation") or {}).get("valid") is True
    candidates_by_backend = {str(candidate["backend"]): candidate for candidate in succeeded}
    classic = candidates_by_backend[running_stack.primary_backend]
    corrector = candidates_by_backend[running_stack.secondary_backend]
    assert corrector.get("parent_candidate_ids") == [classic.get("candidate_id")]
    assert corrector.get("lineage_id") == classic.get("candidate_id")
    assert (corrector.get("provenance") or {}).get("lineage_relation") == "context_derived"
    _assert_stage(payload, stage="candidate_execution", mode="sequential", count=2)
    _assert_stage(payload, stage="fusion", strategy="classic_then_correct")


def test_restricted_worker_kill_fails_closed_and_recovers_with_real_choice(
    running_stack: RunningHardwareStack,
) -> None:
    profile_id = _configure_profile(
        running_stack,
        strategy="parallel_compare",
        restricted_choice=True,
    )
    container_id = _service_container(running_stack, running_stack.restricted_service)
    try:
        killed = _run_compose(running_stack, "kill", running_stack.restricted_service, timeout=30)
        assert killed.returncode == 0, killed.stderr
        stopped = _run_compose(
            running_stack,
            "stop",
            "-t",
            "10",
            running_stack.restricted_service,
            timeout=30,
        )
        assert stopped.returncode == 0, stopped.stderr
        state = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert state.returncode == 0, state.stderr
        assert state.stdout.strip() == "false"
        unavailable = _restricted_status(running_stack)
        assert unavailable.status_code == 503
        assert _api_error_code(unavailable) == "worker_unavailable"

        failed_run_id = f"vprf-worker-down-{uuid.uuid4().hex}"
        baseline = _transcribe(
            running_stack,
            profile_id=profile_id,
            run_id=failed_run_id,
        )
        _assert_real_candidate_provenance(
            baseline,
            expected_backends={running_stack.primary_backend, running_stack.secondary_backend},
        )
        assert baseline.get("fusion_strategy") == "parallel_compare"
        assert "hub_restricted_choice_applied" not in (baseline.get("warnings") or [])
        assert "restricted_choice" not in (baseline.get("decision_trace") or {})
        _assert_failed_restricted_choice_task(
            running_stack,
            voice_payload=baseline,
            profile_id=profile_id,
            run_id=failed_run_id,
        )
    finally:
        _restart_restricted_worker(running_stack)

    recovered = _restricted_status(running_stack)
    recovered.raise_for_status()
    recovered_status = _api_data(recovered).get("restricted_inference")
    assert isinstance(recovered_status, dict) and recovered_status.get("status") == "ready"

    run_id = f"vprf-recovery-{uuid.uuid4().hex}"
    payload = _transcribe(running_stack, profile_id=profile_id, run_id=run_id)
    _assert_real_candidate_provenance(
        payload,
        expected_backends={running_stack.primary_backend, running_stack.secondary_backend},
    )
    _assert_restricted_choice_task(
        running_stack,
        voice_payload=payload,
        profile_id=profile_id,
        run_id=run_id,
    )
