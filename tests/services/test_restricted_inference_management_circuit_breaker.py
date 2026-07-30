from __future__ import annotations

import json

import pytest

from agent.services.restricted_inference_management_circuit_breaker import (
    RestrictedInferenceManagementCircuitBreaker,
)
from agent.services.restricted_inference_management_service import (
    RestrictedInferenceManagementError,
    RestrictedInferenceManagementService,
)


_ENDPOINT = "http://restricted-worker:8091/internal/v1/restricted-inference"
_TOKEN = "restricted-inference-circuit-test-token"


class _Response:
    headers = {"Content-Type": "application/json"}

    def read(self, _limit: int) -> bytes:
        return json.dumps({"removed_entries": 1}).encode()


class _UnavailableOpener:
    def __init__(self) -> None:
        self.calls = 0

    def open(self, _request, _timeout=None, **_kwargs):
        self.calls += 1
        raise TimeoutError("private transport timeout")


def _service(
    *,
    breaker: RestrictedInferenceManagementCircuitBreaker,
    opener,
    resolutions: list[str],
) -> RestrictedInferenceManagementService:
    def resolve(_host: str, _port: int) -> tuple[str, ...]:
        resolutions.append("resolved")
        return ("10.42.0.8",)

    return RestrictedInferenceManagementService(
        inference_endpoint=_ENDPOINT,
        allowed_endpoints=(_ENDPOINT,),
        bearer_token=_TOKEN,
        resolver=resolve,
        opener=opener,
        circuit_breaker=breaker,
    )


def test_first_proven_timeout_coalesces_fresh_hub_clients_for_same_endpoint() -> None:
    now = [100.0]
    breaker = RestrictedInferenceManagementCircuitBreaker(
        open_seconds=60,
        clock=lambda: now[0],
    )
    opener = _UnavailableOpener()
    resolutions: list[str] = []

    with pytest.raises(RestrictedInferenceManagementError) as first:
        _service(
            breaker=breaker,
            opener=opener,
            resolutions=resolutions,
        ).cache_gc()
    with pytest.raises(RestrictedInferenceManagementError) as coalesced:
        _service(
            breaker=breaker,
            opener=opener,
            resolutions=resolutions,
        ).cache_gc()

    assert first.value.reason_code == "worker_unavailable"
    assert coalesced.value.reason_code == "worker_circuit_open"
    assert opener.calls == 1
    assert resolutions == ["resolved"]


def test_cooldown_allows_one_half_open_probe_and_success_closes_circuit() -> None:
    now = [200.0]
    breaker = RestrictedInferenceManagementCircuitBreaker(
        open_seconds=10,
        clock=lambda: now[0],
    )
    breaker.record_unavailable(_ENDPOINT)

    assert breaker.before_request(_ENDPOINT).allowed is False
    now[0] = 210.0
    probe = breaker.before_request(_ENDPOINT)
    concurrent = breaker.before_request(_ENDPOINT)
    assert probe.allowed is True
    assert probe.state == "half_open"
    assert concurrent.allowed is False

    breaker.record_reachable(_ENDPOINT)
    assert breaker.before_request(_ENDPOINT).state == "closed"


def test_availability_failure_does_not_open_a_different_endpoint() -> None:
    breaker = RestrictedInferenceManagementCircuitBreaker(open_seconds=60)
    breaker.record_unavailable(_ENDPOINT)

    other = "http://other-restricted-worker:8091/internal/v1/restricted-inference"
    assert breaker.before_request(_ENDPOINT).allowed is False
    assert breaker.before_request(other).allowed is True
