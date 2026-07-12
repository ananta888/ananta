from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pytest

from agent.services.path_ai_mode_policy_service import (
    AI_MODE_RESTRICTED_TRANSFORMER,
    PathAiModePolicyService,
    PathAiModeRule,
)
from agent.services.restricted_inference_contract import (
    CONTRACT_VERSION,
    RestrictedInferenceOperation,
    RestrictedInferenceRequest,
)
from agent.services.restricted_inference_port import (
    ContractRestrictedInferencePort,
    HttpRestrictedInferenceTransport,
    RestrictedInferenceTransportError,
)
from agent.services.restricted_model_inference_service import (
    RestrictedInferenceInvocationContext,
    RestrictedInferenceRemoteError,
    RestrictedModelInferenceService,
    _port_from_environment,
)

INFERENCE_ENDPOINT = "http://restricted-inference:8091/internal/v1/restricted-inference"
TEST_TOKEN = "restricted-inference-test-token"


class _Transport:
    def __init__(self, *, fail: bool = False) -> None:
        self.request: RestrictedInferenceRequest | None = None
        self.fail = fail

    def dispatch(self, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        self.request = RestrictedInferenceRequest.from_dict(envelope)
        if self.fail:
            return {
                "contract_version": CONTRACT_VERSION,
                "request_id": self.request.request_id,
                "task_id": self.request.task_id,
                "operation": self.request.operation.value,
                "status": "failed",
                "result": None,
                "error": {"code": "model_error", "message": "safe failure", "retryable": False},
                "no_generation": True,
            }
        return {
            "contract_version": CONTRACT_VERSION,
            "request_id": self.request.request_id,
            "task_id": self.request.task_id,
            "operation": self.request.operation.value,
            "status": "succeeded",
            "result": {
                "label": "unsafe",
                "confidence": 0.8,
                "all_scores": {"safe": 0.2, "unsafe": 0.8},
                "engine": "huggingface-transformers",
                "model_id": "fixture/model",
                "manifest_digest": "a" * 64,
                "latency_ms": 2.0,
            },
            "error": None,
            "no_generation": True,
        }


def _context(_operation: str) -> RestrictedInferenceInvocationContext:
    return RestrictedInferenceInvocationContext(
        request_id="request-hub-1",
        task_id="task-hub-1",
        run_id="run-hub-1",
        tenant_id="tenant-a",
        policy_hash="policy-a",
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
        execution_policy={"max_batch_size": 4},
    )


def _worker_request() -> RestrictedInferenceRequest:
    return RestrictedInferenceRequest(
        request_id="request-security-1",
        task_id="task-security-1",
        run_id="run-security-1",
        tenant_id="tenant-security-1",
        operation=RestrictedInferenceOperation.CLASSIFY,
        payload={"text": "ok", "labels": ["safe"]},
        model_manifest_id="manifest-security-1",
        policy_hash="policy-security-1",
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
    )


def test_hub_facade_maps_worker_contract_without_local_adapter_loading() -> None:
    transport = _Transport()
    service = RestrictedModelInferenceService(
        inference_port=ContractRestrictedInferencePort(transport),
        manifest_resolver=lambda _operation: "fixture-manifest-v1",
        invocation_context_provider=_context,
        legacy_local_enabled=False,
        use_mock_fallback=False,
    )

    result = service.classify("unsafe input", ["safe", "unsafe"], path="agent/security.py")

    assert result.label == "unsafe"
    assert transport.request is not None
    assert transport.request.run_id == "run-hub-1"
    assert transport.request.tenant_id == "tenant-a"
    assert transport.request.paths == ("agent/security.py",)
    assert transport.request.execution_policy["max_batch_size"] == 4
    assert service.audit_log()[-1]["manifest_digest"] == "a" * 64


def test_hub_facade_propagates_typed_worker_failure_without_mock_fallback() -> None:
    service = RestrictedModelInferenceService(
        inference_port=ContractRestrictedInferencePort(_Transport(fail=True)),
        manifest_resolver=lambda _operation: "fixture-manifest-v1",
        invocation_context_provider=_context,
        legacy_local_enabled=False,
        use_mock_fallback=True,
    )

    with pytest.raises(RestrictedInferenceRemoteError) as error:
        service.classify("unsafe input", ["safe", "unsafe"], path="agent/security.py")

    assert error.value.code == "model_error"
    assert service.audit_log()[-1]["event"] == "model_inference_blocked"


def test_worker_dispatch_requires_a_real_path_scope() -> None:
    transport = _Transport()
    service = RestrictedModelInferenceService(
        inference_port=ContractRestrictedInferencePort(transport),
        manifest_resolver=lambda _operation: "fixture-manifest-v1",
        invocation_context_provider=_context,
        legacy_local_enabled=False,
        use_mock_fallback=False,
    )

    with pytest.raises(RestrictedModelInferenceService.InferenceBlockedError, match="path scope"):
        service.classify("unsafe input", ["safe", "unsafe"])

    assert transport.request is None


def test_path_limits_are_intersected_into_the_worker_execution_policy() -> None:
    transport = _Transport()
    policy = PathAiModePolicyService(
        rules=[
            PathAiModeRule.from_raw(
                {
                    "path_glob": "secure/**",
                    "allowed_model_engines": ["huggingface-transformers"],
                    "allow_attention": False,
                    "allow_hidden_states": False,
                    "allow_logits": True,
                    "max_batch_size": 1,
                    "max_input_chars": 128,
                }
            )
        ]
    )
    requested = replace(
        _context("classify"),
        execution_policy={
            "allow_attention": True,
            "allow_hidden_states": True,
            "device": "cuda:0",
            "max_batch_size": 4,
            "max_input_chars": 1_000,
        },
    )
    service = RestrictedModelInferenceService(
        inference_port=ContractRestrictedInferencePort(transport),
        manifest_resolver=lambda _operation: "fixture-manifest-v1",
        manifest_engine_resolver=lambda _operation: "huggingface-transformers",
        invocation_context_provider=lambda _operation: requested,
        legacy_local_enabled=False,
        use_mock_fallback=False,
        policy_service=policy,
    )

    service.classify("unsafe input", ["safe", "unsafe"], path="secure/file.py")

    assert transport.request is not None
    assert dict(transport.request.execution_policy) == {
        "allow_attention": False,
        "allow_cpu_fallback": False,
        "allow_hidden_states": False,
        "device": "cuda:0",
        "max_batch_size": 1,
        "max_candidates": 1,
        "max_input_chars": 128,
        "max_output_dimensions": 65_536,
    }


def test_any_blocked_path_prevents_multi_path_worker_delegation() -> None:
    transport = _Transport()
    policy = PathAiModePolicyService(
        rules=[
            PathAiModeRule.from_raw(
                {
                    "path_glob": "secrets/**",
                    "blocked_ai_modes": [AI_MODE_RESTRICTED_TRANSFORMER],
                }
            )
        ]
    )
    service = RestrictedModelInferenceService(
        inference_port=ContractRestrictedInferencePort(transport),
        manifest_resolver=lambda _operation: "fixture-manifest-v1",
        legacy_local_enabled=False,
        use_mock_fallback=False,
        policy_service=policy,
    )
    context = replace(_context("classify"), paths=("agent/ok.py", "secrets/token.txt"))

    with pytest.raises(RestrictedModelInferenceService.InferenceBlockedError):
        service.classify("unsafe input", ["safe", "unsafe"], context=context)

    assert transport.request is None


class _HttpResponse:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self._body = json.dumps(dict(payload)).encode("utf-8")
        self._offset = 0
        self.headers = {"Content-Type": "application/json"}

    def read(self, count: int) -> bytes:
        value = self._body[self._offset : self._offset + count]
        self._offset += len(value)
        return value


class _HttpOpener:
    def __init__(self) -> None:
        self.request = None
        self.timeout = 0.0

    def open(self, request, timeout: float):
        self.request = request
        self.timeout = timeout
        envelope = json.loads(request.data.decode("utf-8"))
        return _HttpResponse(
            {
                "contract_version": CONTRACT_VERSION,
                "request_id": envelope["request_id"],
                "task_id": envelope["task_id"],
                "operation": "classify",
                "status": "succeeded",
                "result": {
                    "label": "safe",
                    "confidence": 1.0,
                    "all_scores": {"safe": 1.0},
                    "engine": "huggingface-transformers",
                    "model_id": "fixture/model",
                    "manifest_digest": "b" * 64,
                    "latency_ms": 1.0,
                },
                "error": None,
                "no_generation": True,
            }
        )


def test_http_transport_is_authenticated_bounded_and_preserves_correlation() -> None:
    opener = _HttpOpener()
    transport = HttpRestrictedInferenceTransport(
        endpoint=INFERENCE_ENDPOINT,
        allowed_endpoints=(INFERENCE_ENDPOINT,),
        bearer_token=TEST_TOKEN,
        resolver=lambda _host, _port: ("10.42.0.8",),
        opener=opener,
    )
    request = RestrictedInferenceRequest(
        request_id="request-1",
        task_id="task-1",
        run_id="run-1",
        tenant_id="tenant-1",
        operation=RestrictedInferenceOperation.CLASSIFY,
        payload={"text": "ok", "labels": ["safe"]},
        model_manifest_id="manifest-1",
        policy_hash="policy-1",
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
    )

    payload = transport.dispatch(request.to_dict())

    assert payload["request_id"] == "request-1"
    assert opener.request.get_header("Authorization") == f"Bearer {TEST_TOKEN}"
    assert opener.request.get_header("Host") == "restricted-inference:8091"
    assert opener.request.full_url.startswith("http://10.42.0.8:8091/")
    assert opener.timeout > 0


def test_http_transport_rejects_credentials_in_endpoint() -> None:
    with pytest.raises(ValueError):
        HttpRestrictedInferenceTransport(
            endpoint="http://user:password@worker:8091/internal/v1/restricted-inference",
            allowed_endpoints=(INFERENCE_ENDPOINT,),
            bearer_token=TEST_TOKEN,
        )

    with pytest.raises(RestrictedInferenceTransportError) as error:
        transport = HttpRestrictedInferenceTransport(
            endpoint="http://worker:8091/internal/v1/restricted-inference",
            allowed_endpoints=("http://worker:8091/internal/v1/restricted-inference",),
            bearer_token=TEST_TOKEN,
        )
        expired = RestrictedInferenceRequest(
            request_id="request-1",
            task_id="task-1",
            tenant_id="tenant-1",
            operation=RestrictedInferenceOperation.CLASSIFY,
            payload={"text": "ok", "labels": ["safe"]},
            model_manifest_id="manifest-1",
            policy_hash="policy-1",
            deadline_epoch_ms=1,
        )
        transport.dispatch(expired.to_dict())
    assert error.value.reason_code == "timeout"


def test_http_transport_rejects_weak_bearer_token() -> None:
    with pytest.raises(ValueError, match="at least 24"):
        HttpRestrictedInferenceTransport(
            endpoint=INFERENCE_ENDPOINT,
            allowed_endpoints=(INFERENCE_ENDPOINT,),
            bearer_token="too-short",
        )


@pytest.mark.parametrize(
    "address",
    [
        "8.8.8.8",
        "127.0.0.1",
        "169.254.169.254",
        "192.0.2.1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
    ],
)
def test_http_transport_rejects_non_container_targets(address: str) -> None:
    opener = _HttpOpener()
    transport = HttpRestrictedInferenceTransport(
        endpoint=INFERENCE_ENDPOINT,
        allowed_endpoints=(INFERENCE_ENDPOINT,),
        bearer_token=TEST_TOKEN,
        resolver=lambda _host, _port: (address,),
        opener=opener,
    )

    with pytest.raises(RestrictedInferenceTransportError) as error:
        transport.dispatch(_worker_request().to_dict())

    assert error.value.reason_code == "worker_address_forbidden"
    assert opener.request is None


def test_http_transport_disables_environment_proxies(monkeypatch) -> None:
    opener = _HttpOpener()
    handlers: tuple[object, ...] = ()

    def build_opener(*values: object) -> _HttpOpener:
        nonlocal handlers
        handlers = values
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)

    HttpRestrictedInferenceTransport(
        endpoint=INFERENCE_ENDPOINT,
        allowed_endpoints=(INFERENCE_ENDPOINT,),
        bearer_token=TEST_TOKEN,
    )

    proxy_handlers = [
        handler for handler in handlers if isinstance(handler, urllib.request.ProxyHandler)
    ]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}


def test_http_transport_rejects_mixed_private_public_dns_answers() -> None:
    transport = HttpRestrictedInferenceTransport(
        endpoint=INFERENCE_ENDPOINT,
        allowed_endpoints=(INFERENCE_ENDPOINT,),
        bearer_token=TEST_TOKEN,
        resolver=lambda _host, _port: ("10.42.0.8", "8.8.8.8"),
        opener=_HttpOpener(),
    )

    with pytest.raises(RestrictedInferenceTransportError) as error:
        transport.dispatch(_worker_request().to_dict())

    assert error.value.reason_code == "worker_address_forbidden"


def test_http_transport_pins_one_private_dns_answer_and_never_re_resolves_in_opener() -> None:
    opener = _HttpOpener()
    resolutions = 0

    def resolver(_host: str, _port: int) -> tuple[str, ...]:
        nonlocal resolutions
        resolutions += 1
        return ("10.42.0.9", "10.42.0.8")

    transport = HttpRestrictedInferenceTransport(
        endpoint=INFERENCE_ENDPOINT,
        allowed_endpoints=(INFERENCE_ENDPOINT,),
        bearer_token=TEST_TOKEN,
        resolver=resolver,
        opener=opener,
    )

    transport.dispatch(_worker_request().to_dict())

    assert resolutions == 1
    assert opener.request.full_url.startswith("http://10.42.0.8:8091/")
    assert opener.request.get_header("Host") == "restricted-inference:8091"


def test_http_transport_rejects_endpoint_not_in_exact_allowlist() -> None:
    with pytest.raises(ValueError, match="exactly allowlisted"):
        HttpRestrictedInferenceTransport(
            endpoint=INFERENCE_ENDPOINT,
            allowed_endpoints=("http://other-worker:8091/internal/v1/restricted-inference",),
            bearer_token=TEST_TOKEN,
        )


def test_environment_factory_fails_closed_without_or_outside_exact_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_URL", INFERENCE_ENDPOINT)
    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_TOKEN", TEST_TOKEN)
    monkeypatch.delenv("ANANTA_RESTRICTED_INFERENCE_ALLOWED_ENDPOINTS", raising=False)

    with pytest.raises(RuntimeError, match="allowlist"):
        _port_from_environment()

    monkeypatch.setenv(
        "ANANTA_RESTRICTED_INFERENCE_ALLOWED_ENDPOINTS",
        "http://other-worker:8091/internal/v1/restricted-inference",
    )
    with pytest.raises(ValueError, match="exactly allowlisted"):
        _port_from_environment()

    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_ALLOWED_ENDPOINTS", INFERENCE_ENDPOINT)
    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_TOKEN", "too-short")
    with pytest.raises(ValueError, match="at least 24"):
        _port_from_environment()

    monkeypatch.setenv("ANANTA_RESTRICTED_INFERENCE_TOKEN", TEST_TOKEN)
    assert _port_from_environment() is not None


def test_hub_facade_import_does_not_import_ml_runtimes() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import agent.services.restricted_model_inference_service
for name in ('torch', 'transformers', 'sentence_transformers', 'onnxruntime'):
    assert name not in sys.modules, name
""",
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
