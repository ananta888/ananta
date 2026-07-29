from __future__ import annotations

from types import SimpleNamespace

import pytest

from worker.retrieval import qdrant_client_port
from worker.retrieval.qdrant_client_port import (
    FilterCondition,
    QdrantClientAdapter,
    QdrantClientError,
    QdrantExtraRequiredError,
    ServerFilter,
)


class _Model:
    def __init__(self, **values):
        self.__dict__.update(values)


class _Models:
    MatchAny = _Model
    MatchValue = _Model
    FieldCondition = _Model
    Filter = _Model


class _RawClient:
    def __init__(self):
        self.query_calls = []

    def get_collections(self):
        return SimpleNamespace(collections=[])

    def query_points(self, **values):
        self.query_calls.append(values)
        return SimpleNamespace(
            points=[
                SimpleNamespace(
                    id="point-1",
                    score=0.75,
                    payload={"record_id": "record-1"},
                )
            ]
        )

    def search(self, **values):
        raise AssertionError(f"legacy search API must not be called: {values}")


class _RemoteInternals:
    def __init__(
        self,
        *,
        host: str,
        grpc_port: int,
        https: bool,
        timeout: float,
    ):
        self._host = host
        self._grpc_port = grpc_port
        self._https = https
        self._grpc_channel_pool = []
        self._timeout = timeout
        self._rest_args = {"timeout": timeout}
        self.openapi_client = SimpleNamespace(
            client=SimpleNamespace(
                _client=SimpleNamespace(timeout=timeout),
            )
        )


class _ConstructedClient(_RawClient):
    def __init__(self, **values):
        super().__init__()
        self.constructor_values = values
        self._client = _RemoteInternals(
            host="rest.example.test",
            grpc_port=int(values.get("grpc_port") or 6334),
            https=True,
            timeout=float(values.get("timeout") or 10.0),
        )


class _ClientModule:
    QdrantClient = _ConstructedClient


class _FailingClientModule:
    class QdrantClient:
        def __init__(self, **values):
            raise RuntimeError(
                "Authorization: constructor-secret "
                f"api_key={values.get('api_key')}"
            )


class _HttpStatusError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


class _ProbeClient(_RawClient):
    def __init__(self, failure: BaseException | None = None):
        super().__init__()
        self.failure = failure

    def get_collections(self):
        if self.failure is not None:
            raise self.failure
        return super().get_collections()


def test_adapter_uses_qdrant_118_query_points_not_legacy_search() -> None:
    raw = _RawClient()
    adapter = QdrantClientAdapter(
        rest_origin="http://localhost:6333",
        raw_client=raw,
        models_module=_Models,
    )
    query_filter = ServerFilter(
        (
            FilterCondition("workspace_id", ("workspace",)),
            FilterCondition("repository_id", ("repository",)),
        )
    )

    result = adapter.query_points(
        "collection",
        query_vector=(1.0, 0.0),
        query_filter=query_filter,
        limit=5,
    )

    assert len(raw.query_calls) == 1
    assert raw.query_calls[0]["query"] == [1.0, 0.0]
    assert raw.query_calls[0]["limit"] == 5
    assert result[0].payload["record_id"] == "record-1"


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_reason"),
    [
        (None, "ready", "ok"),
        (TimeoutError("sensitive timeout detail"), "unavailable", "qdrant_timeout"),
        (
            _HttpStatusError(401, "Authorization: secret-token"),
            "unauthorized",
            "qdrant_unauthorized",
        ),
        (
            RuntimeError("private qdrant hostname and api_key=secret"),
            "unavailable",
            "qdrant_unavailable",
        ),
    ],
)
def test_probe_classifies_health_without_network_or_sensitive_diagnostics(
    failure: BaseException | None,
    expected_status: str,
    expected_reason: str,
) -> None:
    adapter = QdrantClientAdapter(
        rest_origin="http://localhost:6333",
        raw_client=_ProbeClient(failure),
        models_module=_Models,
    )

    availability = adapter.probe()

    assert availability.status == expected_status
    assert availability.reason == expected_reason
    assert "secret" not in repr(availability).lower()


def test_missing_optional_dependency_has_stable_install_reason(monkeypatch) -> None:
    def missing(_name):
        raise ModuleNotFoundError("qdrant-client unavailable")

    monkeypatch.setattr(qdrant_client_port.importlib, "import_module", missing)

    with pytest.raises(QdrantExtraRequiredError) as exc:
        QdrantClientAdapter(rest_origin="http://localhost:6333")

    assert exc.value.reason == "qdrant_extra_required"
    assert "ananta[qdrant]" in str(exc.value)


def test_adapter_honors_separate_rest_and_grpc_hosts(monkeypatch) -> None:
    monkeypatch.setattr(
        qdrant_client_port,
        "_load_qdrant_modules",
        lambda: (_ClientModule, _Models),
    )

    adapter = QdrantClientAdapter(
        rest_origin="https://rest.example.test:6333",
        grpc_origin="grpcs://grpc.example.test:7443",
        connect_timeout_seconds=3,
        timeout_seconds=10,
        prefer_grpc=True,
    )

    remote = adapter._client._client
    assert adapter._client.constructor_values["url"] == "https://rest.example.test:6333"
    assert remote._host == "grpc.example.test"
    assert remote._grpc_port == 7443
    assert remote._https is True
    assert remote._ananta_rest_origin == "https://rest.example.test:6333"
    assert remote._ananta_grpc_origin == "grpcs://grpc.example.test:7443"
    timeout = remote.openapi_client.client._client.timeout
    assert timeout.connect == 3.0
    assert timeout.read == 10.0
    assert timeout.write == 10.0
    assert timeout.pool == 10.0
    assert remote._timeout == 10.0
    assert remote._ananta_grpc_connect_timeout_seconds == 3.0
    assert adapter._client.constructor_values["check_compatibility"] is False
    assert adapter._client.constructor_values["grpc_options"] == {
        "grpc.initial_reconnect_backoff_ms": 1000,
        "grpc.min_reconnect_backoff_ms": 1000,
        "grpc.max_reconnect_backoff_ms": 3000,
    }


def test_adapter_redacts_foreign_constructor_failures(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        qdrant_client_port,
        "_load_qdrant_modules",
        lambda: (_FailingClientModule, _Models),
    )

    with pytest.raises(QdrantClientError) as exc:
        QdrantClientAdapter(
            rest_origin="https://qdrant.example.test:6333",
            api_key="constructor-secret",
        )

    assert exc.value.reason == "qdrant_unavailable"
    assert exc.value.operation == "configure_client"
    assert "constructor-secret" not in str(exc.value)
    assert exc.value.__cause__ is None


def test_adapter_scopes_private_ca_to_qdrant_rest_and_grpc(monkeypatch) -> None:
    class _TrustContext:
        loaded_ca = None

        def load_verify_locations(self, *, cadata):
            self.loaded_ca = cadata

    context = _TrustContext()
    monkeypatch.setattr(
        qdrant_client_port.ssl,
        "create_default_context",
        lambda: context,
    )
    monkeypatch.setattr(
        qdrant_client_port,
        "_load_qdrant_modules",
        lambda: (_ClientModule, _Models),
    )

    adapter = QdrantClientAdapter(
        rest_origin="https://qdrant:6333",
        grpc_origin="grpcs://qdrant:6334",
        tls_ca_cert_pem="private-ca-pem",
    )

    assert context.loaded_ca == "private-ca-pem"
    assert adapter._client.constructor_values["verify"] is context
    assert adapter._client.constructor_values["grpc_options"][
        "root_certificates"
    ] == b"private-ca-pem"


def test_adapter_rejects_invalid_private_ca_without_exposing_it(
    monkeypatch,
) -> None:
    class _RejectingTrustContext:
        def load_verify_locations(self, *, cadata):
            raise ValueError(cadata)

    monkeypatch.setattr(
        qdrant_client_port.ssl,
        "create_default_context",
        _RejectingTrustContext,
    )

    with pytest.raises(QdrantClientError) as exc:
        QdrantClientAdapter(
            rest_origin="https://qdrant:6333",
            tls_ca_cert_pem="sensitive-invalid-ca",
            raw_client=_RawClient(),
            models_module=_Models,
        )

    assert exc.value.reason == "vector_store_tls_ca_cert_invalid"
    assert "sensitive-invalid-ca" not in str(exc.value)


def test_adapter_accepts_only_exact_allowlisted_trusted_private_origin() -> None:
    endpoint = SimpleNamespace(
        rest_url="https://qdrant:6333",
        grpc_url=None,
        allowed_origins=("https://qdrant:6333",),
        trusted_private_origins=("https://qdrant:6333",),
        external_calls_allowed=False,
        tls_verify=True,
        connect_timeout_seconds=2.0,
        request_timeout_seconds=8.0,
        prefer_grpc=False,
    )

    adapter = QdrantClientAdapter.from_endpoint(
        endpoint,
        raw_client=_RawClient(),
        models_module=_Models,
    )

    assert adapter._rest_origin == "https://qdrant:6333"
    with pytest.raises(
        QdrantClientError,
        match="vector_store_tls_policy_violation",
    ):
        QdrantClientAdapter.from_endpoint(
            SimpleNamespace(
                **{
                    **endpoint.__dict__,
                    "rest_url": "http://qdrant:6333",
                    "allowed_origins": ("http://qdrant:6333",),
                    "trusted_private_origins": ("http://qdrant:6333",),
                }
            ),
            raw_client=_RawClient(),
            models_module=_Models,
        )
    with pytest.raises(QdrantClientError):
        QdrantClientAdapter.from_endpoint(
            SimpleNamespace(
                **{
                    **endpoint.__dict__,
                    "allowed_origins": ("http://localhost:6333",),
                }
            ),
            raw_client=_RawClient(),
            models_module=_Models,
        )


def test_foreign_exception_text_is_not_exposed() -> None:
    class _FailingClient(_RawClient):
        def query_points(self, **values):
            raise RuntimeError("Authorization: top-secret api_key=top-secret")

    adapter = QdrantClientAdapter(
        rest_origin="http://localhost:6333",
        raw_client=_FailingClient(),
        models_module=_Models,
    )
    query_filter = ServerFilter((FilterCondition("workspace_id", ("workspace",)),))

    with pytest.raises(QdrantClientError) as exc:
        adapter.query_points(
            "collection",
            query_vector=(1.0,),
            query_filter=query_filter,
            limit=1,
        )

    assert exc.value.reason == "qdrant_unavailable"
    assert "top-secret" not in str(exc.value)
