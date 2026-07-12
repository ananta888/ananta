from __future__ import annotations

import json
import urllib.request

import pytest

from agent.services.restricted_inference_management_service import (
    RestrictedInferenceManagementError,
    RestrictedInferenceManagementService,
)

INFERENCE_ENDPOINT = "http://restricted-worker:8091/internal/v1/restricted-inference"
TEST_TOKEN = "restricted-inference-test-token"


class _Response:
    def __init__(self, payload: dict) -> None:
        self.headers = {"Content-Type": "application/json"}
        self._body = json.dumps(payload).encode()

    def read(self, _limit: int) -> bytes:
        return self._body


class _Opener:
    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = list(payloads)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return _Response(self._payloads.pop(0))


def test_management_transport_redacts_locations_and_sends_only_bounded_configuration_fields() -> None:
    opener = _Opener(
        [
            {
                "status": "ready",
                "model_path": "/private/models/model.bin",
                "capability_catalog": [
                    {
                        "id": "fixture/model",
                        "status": "ready",
                        "remote_note": "https://models.invalid/private",
                        "extensions": {
                            "restricted_inference": {
                                "manifest_id": "fixture-classifier",
                                "manifest_digest": "a" * 64,
                                "worker_url": "http://worker.internal:8091",
                            }
                        },
                    }
                ],
            },
            {
                "schema_version": "ananta.restricted-runtime-config.v1",
                "version": 2,
                "mutable": {"allow_cpu_fallback": True},
                "fixed": {
                    "downloads_allowed": False,
                    "generation_allowed": False,
                    "local_snapshots_only": True,
                    "trust_remote_code": False,
                },
            },
        ]
    )
    service = RestrictedInferenceManagementService(
        inference_endpoint=INFERENCE_ENDPOINT,
        allowed_endpoints=(INFERENCE_ENDPOINT,),
        bearer_token=TEST_TOKEN,
        resolver=lambda _host, _port: ("10.42.0.8",),
        opener=opener,
    )

    status = service.status()
    configuration = service.update_configuration(
        {"allow_cpu_fallback": True},
        expected_version=1,
    )

    assert "model_path" not in status
    capability = status["capability_catalog"][0]
    assert capability["remote_note"] == "[redacted]"
    assert "worker_url" not in capability["extensions"]["restricted_inference"]
    assert TEST_TOKEN not in json.dumps(status)
    assert configuration["version"] == 2
    request, timeout = opener.requests[1]
    assert request.get_method() == "PATCH"
    assert request.full_url.startswith("http://10.42.0.8:8091/")
    assert request.full_url.endswith("/internal/v1/restricted-inference/configuration")
    assert request.get_header("Host") == "restricted-worker:8091"
    assert json.loads(request.data) == {
        "delta": {"allow_cpu_fallback": True},
        "expected_version": 1,
    }
    assert timeout == 5.0


@pytest.mark.parametrize(
    "addresses",
    [
        ("8.8.8.8",),
        ("127.0.0.1",),
        ("169.254.169.254",),
        ("::ffff:127.0.0.1",),
        ("10.42.0.8", "8.8.8.8"),
    ],
)
def test_management_transport_rejects_non_private_or_mixed_dns_before_sending_secret(
    addresses: tuple[str, ...],
) -> None:
    opener = _Opener([])
    service = RestrictedInferenceManagementService(
        inference_endpoint=INFERENCE_ENDPOINT,
        allowed_endpoints=(INFERENCE_ENDPOINT,),
        bearer_token=TEST_TOKEN,
        resolver=lambda _host, _port: addresses,
        opener=opener,
    )

    with pytest.raises(RestrictedInferenceManagementError) as error:
        service.status()

    assert error.value.reason_code == "worker_address_forbidden"
    assert opener.requests == []


def test_management_transport_pins_one_dns_answer_for_the_request() -> None:
    opener = _Opener([{"status": "ready"}])
    resolutions = 0

    def resolver(_host: str, _port: int) -> tuple[str, ...]:
        nonlocal resolutions
        resolutions += 1
        return ("10.42.0.9", "10.42.0.8")

    service = RestrictedInferenceManagementService(
        inference_endpoint=INFERENCE_ENDPOINT,
        allowed_endpoints=(INFERENCE_ENDPOINT,),
        bearer_token=TEST_TOKEN,
        resolver=resolver,
        opener=opener,
    )

    service.status()

    assert resolutions == 1
    request, _timeout = opener.requests[0]
    assert request.full_url.startswith("http://10.42.0.8:8091/")
    assert request.get_header("Host") == "restricted-worker:8091"


def test_management_transport_rejects_nonallowlisted_endpoint_and_weak_token() -> None:
    with pytest.raises(RestrictedInferenceManagementError) as endpoint_error:
        RestrictedInferenceManagementService(
            inference_endpoint=INFERENCE_ENDPOINT,
            allowed_endpoints=(
                "http://other-worker:8091/internal/v1/restricted-inference",
            ),
            bearer_token=TEST_TOKEN,
        )
    assert endpoint_error.value.reason_code == "worker_not_allowlisted"

    with pytest.raises(RestrictedInferenceManagementError) as token_error:
        RestrictedInferenceManagementService(
            inference_endpoint=INFERENCE_ENDPOINT,
            allowed_endpoints=(INFERENCE_ENDPOINT,),
            bearer_token="too-short",
        )
    assert token_error.value.reason_code == "worker_auth_not_configured"


def test_management_transport_disables_proxies_and_installs_redirect_guard(monkeypatch) -> None:
    opener = _Opener([])
    handlers: tuple[object, ...] = ()

    def build_opener(*values: object) -> _Opener:
        nonlocal handlers
        handlers = values
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)

    RestrictedInferenceManagementService(
        inference_endpoint=INFERENCE_ENDPOINT,
        allowed_endpoints=(INFERENCE_ENDPOINT,),
        bearer_token=TEST_TOKEN,
    )

    proxy_handlers = [
        handler for handler in handlers if isinstance(handler, urllib.request.ProxyHandler)
    ]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}
    assert any(type(handler).__name__ == "_NoRedirectHandler" for handler in handlers)
