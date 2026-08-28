from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

import pytest

from agent.services.ml_intern_training_worker_port import (
    WORKER_CONTRACT_VERSION,
    HttpMlInternTrainingWorkerPort,
    MlInternTrainingWorkerTransportError,
    _NoRedirectHandler,
    _path_sha256,
    _validate_artifact_metadata,
    _validate_worker_event,
    _validate_worker_status,
)
from ananta_contracts.unsloth_capability import compose_worker_capability_probe

ENDPOINT = "http://lora-training-worker:8095/internal/v1/lora-training"
TOKEN = "internal-lora-worker-token-at-least-24-characters"


def test_hub_artifact_tree_hash_rejects_symlinks_special_entries_and_empty_trees(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(MlInternTrainingWorkerTransportError) as empty_error:
        _path_sha256(empty)
    assert empty_error.value.reason_code == "adapter_artifact_invalid"

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    (unsafe / "target").write_bytes(b"adapter")
    (unsafe / "adapter_model.safetensors").symlink_to("target")
    with pytest.raises(MlInternTrainingWorkerTransportError) as symlink_error:
        _path_sha256(unsafe)
    assert symlink_error.value.reason_code == "adapter_artifact_invalid"

    (unsafe / "adapter_model.safetensors").unlink()
    os.mkfifo(unsafe / "adapter_model.safetensors")
    with pytest.raises(MlInternTrainingWorkerTransportError) as special_error:
        _path_sha256(unsafe)
    assert special_error.value.reason_code == "adapter_artifact_invalid"


def _safetensors_bytes(payload: bytes = b"\x00\x00\x00\x00") -> bytes:
    header = json.dumps(
        {"lora.weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, len(payload)]}},
        separators=(",", ":"),
    ).encode()
    return len(header).to_bytes(8, "little") + header + payload


class _Response:
    def __init__(self, body: bytes, *, content_type: str, sha256: str | None = None) -> None:
        self._body = body
        self._offset = 0
        self.headers = {"Content-Type": content_type}
        if sha256:
            self.headers["X-Artifact-SHA256"] = sha256

    @classmethod
    def json(cls, payload: Mapping[str, Any]) -> "_Response":
        return cls(json.dumps(dict(payload)).encode(), content_type="application/json")

    def read(self, count: int = -1) -> bytes:
        if count < 0:
            count = len(self._body) - self._offset
        chunk = self._body[self._offset : self._offset + count]
        self._offset += len(chunk)
        return chunk


class _EvaluationOpener:
    def __init__(
        self,
        *,
        stale_status: bool = False,
        wrong_artifact_media: bool = False,
        wrong_artifact_hash: bool = False,
    ) -> None:
        self.envelope: dict[str, Any] | None = None
        self.requests: list[Any] = []
        self.stale_status = stale_status
        self.wrong_artifact_media = wrong_artifact_media
        self.wrong_artifact_hash = wrong_artifact_hash
        self.metrics = {
            "validation_records": 1,
            "base": {"eval_loss": 1.0, "perplexity": 2.718},
            "adapter": {"eval_loss": 0.75, "perplexity": 2.117},
            "delta": {"eval_loss": -0.25, "perplexity": -0.601},
        }
        self.artifact_bodies = {
            "eval_report.json": json.dumps(self.metrics, sort_keys=True).encode(),
            "evaluation.json": json.dumps(self.metrics, sort_keys=True).encode(),
            "evaluation_manifest.json": json.dumps({"schema_version": "test"}, sort_keys=True).encode(),
        }

    def open(self, request, timeout: float):
        del timeout
        self.requests.append(request)
        parsed = urllib.parse.urlsplit(request.full_url)
        path = parsed.path
        if request.method == "GET" and path.endswith("/capabilities"):
            return _Response.json(
                compose_worker_capability_probe(
                    contract_version=WORKER_CONTRACT_VERSION,
                    resource_profile="nvidia",
                    active_gpu_profile="rtx3080-safe",
                    backend_availability={
                        backend: (True, None)
                        for backend in (
                            "mock",
                            "peft_trl",
                            "unsloth",
                            "unsloth_vision",
                            "unsloth_audio",
                            "unsloth_embedding",
                        )
                    },
                    package_versions={
                        "torch": "2.7.0",
                        "unsloth": "2026.7",
                        "unsloth_zoo": "2026.7",
                    },
                    hardware={
                        "cuda_available": True,
                        "torch_version": "2.7.0",
                        "cuda_version": "12.8",
                        "device_count": 1,
                        "device_name": "RTX 3080",
                        "total_vram_bytes": 10 * 1024**3,
                    },
                    runtime_ready=True,
                )
            )
        if request.method == "POST" and path.endswith("/evaluations"):
            self.envelope = json.loads(request.data)
            return _Response.json(self._status("queued"))
        if path.endswith("/events"):
            return _Response.json(
                {
                    "contract_version": WORKER_CONTRACT_VERSION,
                    "job_id": "job-eval-1",
                    "attempt_id": "attempt-eval-1",
                    "events": [],
                    "next_sequence": 0,
                }
            )
        if request.method == "POST" and path.endswith("/heartbeat"):
            return _Response.json(self._status("running"))
        if request.method == "GET" and path.endswith("/jobs/job-eval-1"):
            status = self._status("succeeded")
            if self.stale_status:
                status["attempt_id"] = "stale-attempt"
            status["metrics"] = self.metrics
            status["artifacts"] = [
                {
                    "name": name,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body),
                    "media_type": "application/json",
                }
                for name, body in self.artifact_bodies.items()
            ]
            return _Response.json(status)
        marker = "/artifacts/"
        if marker in path:
            name = urllib.parse.unquote(path.split(marker, 1)[1])
            body = self.artifact_bodies[name]
            return _Response(
                body,
                content_type="text/plain" if self.wrong_artifact_media else "application/json",
                sha256="0" * 64 if self.wrong_artifact_hash else hashlib.sha256(body).hexdigest(),
            )
        raise AssertionError(f"unexpected worker request: {request.method} {request.full_url}")

    def _status(self, status: str) -> dict[str, Any]:
        return {
            "contract_version": WORKER_CONTRACT_VERSION,
            "job_id": "job-eval-1",
            "attempt_id": "attempt-eval-1",
            "fencing_token": 17,
            "correlation_id": str((self.envelope or {}).get("correlation_id") or "correlation-1"),
            "job_type": "evaluate_existing_adapter",
            "backend": "mock",
            "status": status,
            "created_at": 1.0,
            "updated_at": 2.0,
            "heartbeat_at": 2.0,
            "progress": {},
            "metrics": {},
            "artifacts": [],
            "storage_usage": None,
            "resume_checkpoint": None,
            "cancel_mode": None,
            "error": None,
        }


def _port(tmp_path: Path, opener: _EvaluationOpener) -> tuple[HttpMlInternTrainingWorkerPort, Path, Path]:
    datasets = tmp_path / "datasets"
    workspaces = tmp_path / "workspaces"
    artifacts = tmp_path / "artifacts"
    adapter = artifacts / "adapter-imports" / "adapter-one"
    for path in (datasets, workspaces, adapter):
        path.mkdir(parents=True)
    (adapter / "adapter_config.json").write_text('{"base_model_name_or_path":"local/base"}', encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(_safetensors_bytes())
    validation = datasets / "validation.jsonl"
    validation.write_text('{"instruction":"hello","output":"world"}\n', encoding="utf-8")
    train = datasets / "train.jsonl"
    train.write_text('{"instruction":"train","output":"row"}\n', encoding="utf-8")
    port = HttpMlInternTrainingWorkerPort(
        endpoint=ENDPOINT,
        allowed_endpoints=(ENDPOINT,),
        bearer_token=TOKEN,
        dataset_root=datasets,
        workspace_root=workspaces,
        artifact_root=artifacts,
        model_catalog={"local/base": {"relative_path": "local-base", "snapshot_hash": "b" * 64}},
        adapter_resolver=lambda _adapter_id, _tenant_scope_digest: adapter,
        resolver=lambda _host, _port: ("10.42.0.9",),
        opener=opener,
        clock=lambda: 1_000.0,
        sleeper=lambda _seconds: None,
    )
    return port, train, validation


def _execute(port: HttpMlInternTrainingWorkerPort, train: Path, validation: Path) -> Mapping[str, Any]:
    return port.execute(
        job_id="job-eval-1",
        spec={
            "job_type": "evaluate_lora",
            "dataset_id": "dataset-one",
            "adapter_id": "adapter-one",
            "base_model": "local/base",
            "backend": "mock",
            "method": "lora",
            "scorer_name": "ananta_todo_json",
            "_tenant_scope_digest": "a" * 64,
            "hyperparameters": {"seed": 7, "batch_size": 1, "max_seq_length": 256},
        },
        dataset_path=train,
        validation_path=validation,
        attempt_id="attempt-eval-1",
        fencing_token=17,
        on_event=lambda _event: None,
        cancel_check=lambda: False,
    )


def test_evaluation_is_correlated_staged_and_downloaded_with_hashes(tmp_path: Path) -> None:
    opener = _EvaluationOpener()
    port, train, validation = _port(tmp_path, opener)

    result = _execute(port, train, validation)

    assert result["status"] == "completed"
    assert result["adapter_id"] == "adapter-one"
    assert result["metrics"]["adapter"]["eval_loss"] == 0.75
    assert opener.envelope is not None
    assert opener.envelope["job_type"] == "evaluate_existing_adapter"
    assert opener.envelope["resource_profile"] == "nvidia"
    assert opener.envelope["adapter"]["relative_path"] == "adapter"
    assert opener.envelope["validation_dataset"]["validation"]["record_count"] == 1
    assert opener.envelope["configuration"]["scorer_name"] == "ananta_todo_json"
    assert TOKEN not in json.dumps(opener.envelope)
    assert not {"authorization", "bearer_token", "worker_url"}.intersection(opener.envelope)
    tenant_attempt = (
        f"tenants/{'a' * 64}/jobs/job-eval-1/"
        "attempts/attempt-eval-1"
    )
    assert (
        tmp_path
        / "workspaces"
        / tenant_attempt
        / "workspace/adapter/adapter_model.safetensors"
    ).is_file()
    assert (
        tmp_path
        / "artifacts"
        / tenant_attempt
        / "artifacts/evaluation_manifest.json"
    ).is_file()
    first = opener.requests[0]
    assert first.get_header("Authorization") == f"Bearer {TOKEN}"
    assert first.get_header("Host") == "lora-training-worker:8095"
    assert first.full_url.startswith("http://10.42.0.9:8095/")


def test_worker_capability_requires_matching_resource_profile(tmp_path: Path) -> None:
    port, _train, _validation = _port(tmp_path, _EvaluationOpener())
    assert port.supports(job_type="train_lora", backend="peft_trl", gpu_profile="rtx3080-safe") is True
    assert port.supports(job_type="train_lora", backend="peft_trl", gpu_profile="none") is False


def test_worker_capability_probe_fails_closed_on_missing_schema_fields(tmp_path: Path) -> None:
    class _InvalidProbeOpener(_EvaluationOpener):
        def open(self, request, timeout: float):
            if urllib.parse.urlsplit(request.full_url).path.endswith("/capabilities"):
                return _Response.json({"schema_version": "ananta.unsloth-worker-capabilities.v1"})
            return super().open(request, timeout)

    port, _train, _validation = _port(tmp_path, _InvalidProbeOpener())

    assert port.supports(
        job_type="train_lora",
        backend="unsloth",
        gpu_profile="rtx3080-safe",
    ) is False


def test_worker_delegation_requires_opaque_tenant_scope_binding(tmp_path: Path) -> None:
    port, train, validation = _port(tmp_path, _EvaluationOpener())
    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        port.execute(
            job_id="job-eval-1",
            spec={
                "job_type": "evaluate_lora",
                "dataset_id": "dataset-one",
                "adapter_id": "adapter-one",
                "base_model": "local/base",
                "backend": "mock",
            },
            dataset_path=train,
            validation_path=validation,
            attempt_id="attempt-eval-1",
            fencing_token=17,
            on_event=lambda _event: None,
            cancel_check=lambda: False,
        )
    assert error.value.reason_code == "tenant_scope_binding_required"


def test_stale_worker_status_is_fenced(tmp_path: Path) -> None:
    port, train, validation = _port(tmp_path, _EvaluationOpener(stale_status=True))
    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        _execute(port, train, validation)
    assert error.value.reason_code == "worker_correlation_mismatch"


def test_artifact_content_type_must_match_manifest(tmp_path: Path) -> None:
    port, train, validation = _port(tmp_path, _EvaluationOpener(wrong_artifact_media=True))
    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        _execute(port, train, validation)
    assert error.value.reason_code == "artifact_content_type_mismatch"


def test_artifact_header_hash_must_match_manifest(tmp_path: Path) -> None:
    port, train, validation = _port(tmp_path, _EvaluationOpener(wrong_artifact_hash=True))
    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        _execute(port, train, validation)
    assert error.value.reason_code == "artifact_hash_mismatch"
    assert error.value.retryable is False


def test_training_resume_checkpoint_is_forwarded_and_reported_to_hub(tmp_path: Path) -> None:
    opener = _EvaluationOpener()
    port, train, validation = _port(tmp_path, opener)
    checkpoint = {
        "relative_path": "jobs/job-train-1/attempts/attempt-train-1/checkpoints/checkpoint-1.json",
        "binding": {
            "job_id": "job-train-1",
            "source_attempt_id": "attempt-train-1",
            "base_model_hash": "b" * 64,
            "dataset_hash": "c" * 64,
            "configuration_hash": "d" * 64,
            "checkpoint_sha256": "e" * 64,
        },
    }
    captured_envelope: dict[str, Any] = {}
    events: list[Mapping[str, Any]] = []

    def worker_response(method: str, path: str, payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if method == "POST" and path == "/jobs":
            assert payload is not None
            captured_envelope.update(payload)
            correlated = {
                "contract_version": WORKER_CONTRACT_VERSION,
                "job_id": "job-train-1",
                "attempt_id": "attempt-train-2",
                "fencing_token": 2,
                "correlation_id": str(captured_envelope["correlation_id"]),
                "job_type": "train_lora",
                "backend": "mock",
                "created_at": 1.0,
                "updated_at": 2.0,
                "heartbeat_at": 2.0,
                "progress": {},
                "metrics": {},
                "artifacts": [],
                "storage_usage": None,
                "resume_checkpoint": None,
                "cancel_mode": None,
                "error": None,
            }
            return {**correlated, "status": "queued"}
        correlated = {
            "contract_version": WORKER_CONTRACT_VERSION,
            "job_id": "job-train-1",
            "attempt_id": "attempt-train-2",
            "fencing_token": 2,
            "correlation_id": str(captured_envelope["correlation_id"]),
            "job_type": "train_lora",
            "backend": "mock",
            "created_at": 1.0,
            "updated_at": 2.0,
            "heartbeat_at": 2.0,
            "progress": {},
            "metrics": {},
            "artifacts": [],
            "storage_usage": None,
            "resume_checkpoint": None,
            "cancel_mode": None,
            "error": None,
        }
        if method == "GET" and path.endswith("/events?after_sequence=0&limit=200"):
            return {
                "contract_version": WORKER_CONTRACT_VERSION,
                "job_id": "job-train-1",
                "attempt_id": "attempt-train-2",
                "events": [],
                "next_sequence": 0,
            }
        if method == "POST" and path.endswith("/heartbeat"):
            return {**correlated, "status": "running"}
        if method == "GET" and path == "/jobs/job-train-1":
            return {
                **correlated,
                "status": "succeeded",
                "metrics": {},
                "artifacts": [],
                "storage_usage": None,
                "resume_checkpoint": checkpoint,
            }
        raise AssertionError(f"unexpected worker request: {method} {path}")

    port.capability_probe()
    port._request_json = worker_response  # type: ignore[method-assign]  # noqa: SLF001
    port._download_artifacts = (  # type: ignore[method-assign]  # noqa: SLF001
        lambda _job_id,
        _artifacts,
            *,
            job_type,
            backend,
            attempt_id,
        tenant_scope_digest: []
    )

    result = port.execute(
        job_id="job-train-1",
        spec={
            "job_type": "train_lora",
            "dataset_id": "dataset-one",
            "base_model": "local/base",
            "backend": "mock",
            "method": "lora",
            "_tenant_scope_digest": "a" * 64,
            "hyperparameters": {"max_steps": 1},
            "resume_checkpoint": checkpoint,
        },
        dataset_path=train,
        validation_path=validation,
        attempt_id="attempt-train-2",
        fencing_token=2,
        on_event=events.append,
        cancel_check=lambda: False,
    )

    assert captured_envelope["resume_checkpoint"] == checkpoint
    assert result["resume_checkpoint"] == checkpoint
    assert [event["resume_checkpoint"] for event in events if event.get("type") == "checkpoint"] == [checkpoint]


@pytest.mark.parametrize("address", ["8.8.8.8", "127.0.0.1", "169.254.169.254"])
def test_transport_rejects_non_container_addresses(tmp_path: Path, address: str) -> None:
    opener = _EvaluationOpener()
    port, train, validation = _port(tmp_path, opener)
    port._resolver = lambda _host, _port: (address,)  # noqa: SLF001 - focused transport policy test
    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        _execute(port, train, validation)
    assert error.value.reason_code == "worker_address_forbidden"
    assert opener.requests == []


def test_transport_requires_exact_allowlist_and_strong_token(tmp_path: Path) -> None:
    roots = [tmp_path / name for name in ("datasets", "workspaces", "artifacts")]
    for root in roots:
        root.mkdir()
    values = {
        "endpoint": ENDPOINT,
        "allowed_endpoints": ("http://other:8095/internal/v1/lora-training",),
        "bearer_token": TOKEN,
        "dataset_root": roots[0],
        "workspace_root": roots[1],
        "artifact_root": roots[2],
        "model_catalog": {"local/base": {"relative_path": "base", "snapshot_hash": "b" * 64}},
    }
    with pytest.raises(ValueError, match="exactly allowlisted"):
        HttpMlInternTrainingWorkerPort(**values)
    values["allowed_endpoints"] = (ENDPOINT,)
    values["bearer_token"] = "short"
    with pytest.raises(ValueError, match="at least 24"):
        HttpMlInternTrainingWorkerPort(**values)


def test_transport_re_resolves_and_rejects_dns_rebinding(tmp_path: Path) -> None:
    opener = _EvaluationOpener()
    port, train, validation = _port(tmp_path, opener)
    addresses = iter([("10.42.0.9",), ("8.8.8.8",)])
    port._resolver = lambda _host, _port: next(addresses)  # noqa: SLF001

    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        _execute(port, train, validation)

    assert error.value.reason_code == "worker_address_forbidden"
    assert len(opener.requests) == 1


def test_redirect_handler_and_default_opener_disable_redirects_and_proxies(tmp_path: Path) -> None:
    with pytest.raises(MlInternTrainingWorkerTransportError) as redirect:
        _NoRedirectHandler().redirect_request(None, None, 302, "Found", {}, "http://public.invalid")
    assert redirect.value.reason_code == "worker_redirect_forbidden"
    assert redirect.value.retryable is False

    datasets, workspaces, artifacts = (tmp_path / name for name in ("datasets", "workspaces", "artifacts"))
    for root in (datasets, workspaces, artifacts):
        root.mkdir()
    port = HttpMlInternTrainingWorkerPort(
        endpoint=ENDPOINT,
        allowed_endpoints=(ENDPOINT,),
        bearer_token=TOKEN,
        dataset_root=datasets,
        workspace_root=workspaces,
        artifact_root=artifacts,
        model_catalog={"local/base": {"relative_path": "base", "snapshot_hash": "b" * 64}},
    )
    proxy_handlers = [
        handler for handler in port._opener.handlers if isinstance(handler, urllib.request.ProxyHandler)
    ]
    # Passing the explicit empty ProxyHandler suppresses urllib's environment
    # proxy handler; urllib omits the empty handler from the final chain.
    assert proxy_handlers == []


def test_transport_enforces_absolute_deadline_and_sends_bounded_cancel(tmp_path: Path) -> None:
    port, train, validation = _port(tmp_path, _EvaluationOpener())
    captured: dict[str, Any] = {}
    requests: list[tuple[str, str]] = []
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 1_000.0 if clock_calls < 5 else 1_061.0

    def status(state: str) -> dict[str, Any]:
        return {
            "contract_version": WORKER_CONTRACT_VERSION,
            "job_id": "job-eval-1",
            "attempt_id": "attempt-eval-1",
            "fencing_token": 17,
            "correlation_id": str(captured["correlation_id"]),
            "job_type": "evaluate_existing_adapter",
            "backend": "mock",
            "status": state,
            "created_at": 1.0,
            "updated_at": 2.0,
            "heartbeat_at": 2.0,
            "progress": {},
            "metrics": {},
            "artifacts": [],
            "storage_usage": None,
            "resume_checkpoint": None,
            "cancel_mode": None,
            "error": None,
        }

    def response(method: str, path: str, payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
        requests.append((method, path))
        if method == "POST" and path == "/evaluations":
            assert payload is not None
            captured.update(payload)
            return status("queued")
        if method == "GET" and path.endswith("/events?after_sequence=0&limit=200"):
            return {
                "contract_version": WORKER_CONTRACT_VERSION,
                "job_id": "job-eval-1",
                "attempt_id": "attempt-eval-1",
                "events": [],
                "next_sequence": 0,
            }
        if method == "POST" and path.endswith("/heartbeat"):
            return status("running")
        if method == "GET" and path == "/jobs/job-eval-1":
            return status("running")
        if method == "POST" and path.endswith("/cancel"):
            return status("cancel_requested")
        raise AssertionError(f"unexpected request: {method} {path}")

    port.capability_probe()
    port._clock = clock  # noqa: SLF001
    port._timeout_seconds = 60  # noqa: SLF001
    port._request_json = response  # type: ignore[method-assign]  # noqa: SLF001

    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        _execute(port, train, validation)

    assert error.value.reason_code == "timeout"
    assert requests[-1] == ("POST", "/jobs/job-eval-1/cancel")


def test_transport_rejects_oversized_json_before_parsing(tmp_path: Path) -> None:
    port, _train, _validation = _port(tmp_path, _EvaluationOpener())

    class _OversizedOpener:
        def open(self, _request: Any, timeout: float) -> _Response:
            del timeout
            return _Response(b"{" + b"x" * 2_048 + b"}", content_type="application/json")

    port._opener = _OversizedOpener()  # noqa: SLF001
    port._max_response_bytes = 1_024  # noqa: SLF001
    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        port._request_json("GET", "/jobs/job-eval-1", None)  # noqa: SLF001
    assert error.value.reason_code == "worker_response_too_large"
    assert error.value.retryable is False


@pytest.mark.parametrize("retryable", [False, True])
def test_transport_preserves_worker_retry_classification(tmp_path: Path, retryable: bool) -> None:
    port, _train, _validation = _port(tmp_path, _EvaluationOpener())
    payload = {
        "contract_version": WORKER_CONTRACT_VERSION,
        "status": "failed",
        "error": {"code": "queue_full", "message": "worker queue is full", "retryable": retryable},
    }

    class _ErrorOpener:
        def open(self, _request: Any, timeout: float) -> _Response:
            del timeout
            return _Response.json(payload)

    port._opener = _ErrorOpener()  # noqa: SLF001
    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        port._request_json("POST", "/jobs", {})  # noqa: SLF001
    assert error.value.reason_code == "queue_full"
    assert error.value.retryable is retryable


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity", b"1e9999"])
def test_transport_json_parser_rejects_non_finite_numbers(tmp_path: Path, constant: bytes) -> None:
    port, _train, _validation = _port(tmp_path, _EvaluationOpener())

    class _NonFiniteOpener:
        def open(self, _request: Any, timeout: float) -> _Response:
            del timeout
            return _Response(b'{"metric":' + constant + b"}", content_type="application/json")

    port._opener = _NonFiniteOpener()  # noqa: SLF001 - focused adversarial parser test

    with pytest.raises(MlInternTrainingWorkerTransportError) as error:
        port._request_json("GET", "/jobs/job-eval-1", None)  # noqa: SLF001

    assert error.value.reason_code == "invalid_worker_response"
    assert error.value.retryable is False


def test_worker_result_validation_rejects_unknown_nested_and_non_finite_values() -> None:
    status = {
        "contract_version": WORKER_CONTRACT_VERSION,
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "fencing_token": 1,
        "correlation_id": "correlation-1",
        "job_type": "train_lora",
        "backend": "mock",
        "status": "succeeded",
        "created_at": 1.0,
        "updated_at": 2.0,
        "heartbeat_at": 2.0,
        "progress": {},
        "metrics": {"eval_loss": float("nan")},
        "artifacts": [],
        "storage_usage": None,
        "resume_checkpoint": None,
        "cancel_mode": None,
        "error": None,
    }
    with pytest.raises(MlInternTrainingWorkerTransportError, match="non-finite"):
        _validate_worker_status(status)

    finite_status = {**status, "metrics": {}}
    finite_status.pop("heartbeat_at")
    with pytest.raises(MlInternTrainingWorkerTransportError, match="missing required"):
        _validate_worker_status(finite_status)

    artifact = {
        "name": "adapter.safetensors",
        "sha256": "a" * 64,
        "size_bytes": 1,
        "media_type": "application/octet-stream",
        "source_path": "/private/adapter.safetensors",
    }
    with pytest.raises(MlInternTrainingWorkerTransportError, match="unknown fields"):
        _validate_artifact_metadata(artifact)

    event = {
        "contract_version": WORKER_CONTRACT_VERSION,
        "sequence": 1,
        "timestamp": 1.0,
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "fencing_token": 1,
        "correlation_id": "correlation-1",
        "type": "progress",
        "payload": {"step": 1, "max_steps": 2, "prompt": "private"},
    }
    with pytest.raises(MlInternTrainingWorkerTransportError, match="unknown fields"):
        _validate_worker_event(event)


def test_worker_cancel_mode_is_strict_and_normalized_for_hub(tmp_path: Path) -> None:
    port, _train, _validation = _port(tmp_path, _EvaluationOpener())
    status = {
        "contract_version": WORKER_CONTRACT_VERSION,
        "job_id": "job-eval-1",
        "attempt_id": "attempt-eval-1",
        "fencing_token": 17,
        "correlation_id": "correlation-1",
        "job_type": "evaluate_existing_adapter",
        "backend": "mock",
        "status": "cancelled",
        "created_at": 1.0,
        "updated_at": 2.0,
        "heartbeat_at": 2.0,
        "progress": {},
        "metrics": {},
        "artifacts": [],
        "storage_usage": None,
        "resume_checkpoint": None,
        "cancel_mode": "graceful",
        "error": None,
    }

    result = port._terminal_result("job-eval-1", "evaluate_lora", status, {})  # noqa: SLF001
    assert result["cancel_mode"] == "cooperative"

    status["cancel_mode"] = "shell"
    with pytest.raises(MlInternTrainingWorkerTransportError, match="cancel mode"):
        port._terminal_result("job-eval-1", "evaluate_lora", status, {})  # noqa: SLF001
