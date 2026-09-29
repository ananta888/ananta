from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.services.temporal_client_connection import (
    TemporalHubClientConfigurationError,
    TemporalHubClientSecurity,
)
from agent.services.temporal_workflow_backend import TemporalWorkflowBackend


def _secret(path: Path, value: str | bytes) -> str:
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")
    path.chmod(0o440)
    return str(path)


def test_hub_client_reads_rotatable_api_key_and_mtls_files(tmp_path: Path) -> None:
    api_key = _secret(tmp_path / "api-key", "temporal-api-key-value-123456789")
    ca = _secret(tmp_path / "ca.pem", b"test-ca")
    certificate = _secret(tmp_path / "client.pem", b"test-certificate")
    private_key = _secret(tmp_path / "client-key.pem", b"test-private-key")
    config = TemporalHubClientSecurity.from_env(
        {
            "ANANTA_TEMPORAL_HUB_IDENTITY": "hub-runtime-test",
            "ANANTA_TEMPORAL_TLS_ENABLED": "true",
            "ANANTA_TEMPORAL_TLS_SERVER_NAME": "temporal.internal",
            "ANANTA_TEMPORAL_TLS_CA_FILE": ca,
            "ANANTA_TEMPORAL_TLS_CERT_FILE": certificate,
            "ANANTA_TEMPORAL_TLS_KEY_FILE": private_key,
            "ANANTA_TEMPORAL_API_KEY_FILE": api_key,
        }
    )

    kwargs = config.client_kwargs()

    assert kwargs["identity"] == "hub-runtime-test"
    assert kwargs["api_key"] == "temporal-api-key-value-123456789"
    assert kwargs["tls"].server_root_ca_cert == b"test-ca"
    assert kwargs["tls"].client_cert == b"test-certificate"
    assert kwargs["tls"].client_private_key == b"test-private-key"
    assert kwargs["tls"].domain == "temporal.internal"


@pytest.mark.parametrize(
    "environment,reason",
    [
        ({"ANANTA_TEMPORAL_TLS_ENABLED": "sometimes"}, "must be a boolean"),
        ({"ANANTA_TEMPORAL_TLS_CA_FILE": "/run/secrets/ca"}, "require ANANTA_TEMPORAL_TLS_ENABLED"),
        (
            {
                "ANANTA_TEMPORAL_TLS_ENABLED": "true",
                "ANANTA_TEMPORAL_TLS_CERT_FILE": "/run/secrets/cert",
            },
            "configured together",
        ),
        ({"ANANTA_TEMPORAL_API_KEY_FILE": "relative-key"}, "absolute paths"),
    ],
)
def test_hub_client_rejects_ambiguous_or_partial_security_configuration(
    environment: dict[str, str],
    reason: str,
) -> None:
    with pytest.raises(TemporalHubClientConfigurationError, match=reason):
        TemporalHubClientSecurity.from_env(environment)


def test_temporal_backend_connects_with_hub_security_configuration(monkeypatch) -> None:
    monkeypatch.setenv("ANANTA_TEMPORAL_HUB_IDENTITY", "hub-connection-test")
    connect = AsyncMock(return_value=object())

    with patch("temporalio.client.Client.connect", connect):
        import asyncio

        result = asyncio.run(
            TemporalWorkflowBackend(
                address="temporal.internal:7233",
                namespace="tenant-runtime",
            )._client()
        )

    assert result is connect.return_value
    connect.assert_awaited_once_with(
        "temporal.internal:7233",
        namespace="tenant-runtime",
        identity="hub-connection-test",
        api_key=None,
        tls=None,
    )
