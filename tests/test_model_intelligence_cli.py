from __future__ import annotations

import json

import pytest

from agent.cli.model_intelligence import (
    EXIT_AUTH,
    HttpResponse,
    ModelIntelligenceApiClient,
    ModelIntelligenceCliError,
    _exit_code,
)


class _Transport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def send(self, **kwargs) -> HttpResponse:
        self.calls.append(kwargs)
        return self.response


def test_cli_client_uses_only_public_api_contract() -> None:
    transport = _Transport(
        HttpResponse(
            status_code=201,
            body=json.dumps({"job_id": "job-1", "status": "queued"}).encode(),
        )
    )
    client = ModelIntelligenceApiClient(
        base_url="https://ananta.test",
        bearer_token="secret",
        transport=transport,
    )

    result = client.create_job(
        {"job_type": "static"},
        idempotency_key="request-1",
    )

    assert result["job_id"] == "job-1"
    assert transport.calls[0]["url"].endswith(
        "/api/model-intelligence/jobs"
    )
    assert transport.calls[0]["headers"]["Idempotency-Key"] == "request-1"
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret"


def test_cli_client_enforces_server_page_contract_locally() -> None:
    client = ModelIntelligenceApiClient(
        base_url="https://ananta.test",
        transport=_Transport(HttpResponse(200, b"{}")),
    )

    with pytest.raises(ValueError):
        client.list_jobs(page_size=101)


def test_cli_client_maps_structured_api_failure() -> None:
    client = ModelIntelligenceApiClient(
        base_url="https://ananta.test",
        transport=_Transport(
            HttpResponse(
                403,
                b'{"reason_code":"permission_denied","message":"denied"}',
            )
        ),
    )

    with pytest.raises(ModelIntelligenceCliError) as captured:
        client.get_job("job-1")

    assert captured.value.reason_code == "permission_denied"
    assert _exit_code(captured.value) == EXIT_AUTH
