from __future__ import annotations

import importlib
import ipaddress
import math
import ssl
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable
from urllib.parse import urlsplit

from worker.retrieval.vector_store_endpoint_policy import (
    VectorStoreEndpointPolicyError,
    normalize_trusted_private_origins,
)

QDRANT_EXTRA_REQUIRED = "qdrant_extra_required"
QDRANT_UNAVAILABLE = "qdrant_unavailable"
QDRANT_TIMEOUT = "qdrant_timeout"
QDRANT_UNAUTHORIZED = "qdrant_unauthorized"
COLLECTION_MISSING = "collection_missing"
INVALID_ORIGIN = "vector_store_invalid_origin"
TLS_POLICY_VIOLATION = "vector_store_tls_policy_violation"


class QdrantClientError(RuntimeError):
    def __init__(self, reason: str, *, operation: str, retryable: bool = False):
        self.reason = str(reason)
        self.operation = str(operation)
        self.retryable = bool(retryable)
        super().__init__(f"{self.operation} failed ({self.reason})")


class QdrantExtraRequiredError(QdrantClientError):
    def __init__(self) -> None:
        super().__init__(QDRANT_EXTRA_REQUIRED, operation="load_client")
        self.install_hint = "pip install 'ananta[qdrant]'"
        self.args = (
            f"{self.operation} failed ({self.reason}); install with {self.install_hint}",
        )


@dataclass(frozen=True, slots=True)
class ClientPoint:
    point_id: str
    vector: tuple[float, ...]
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ClientScoredPoint:
    point_id: str
    score: float
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ClientCollectionInfo:
    name: str
    dimensions: int
    distance: str
    points_count: int


@dataclass(frozen=True, slots=True)
class FilterCondition:
    key: str
    values: tuple[Any, ...]
    match: str = "value"

    def __post_init__(self) -> None:
        if not self.key or self.match not in {"value", "any"} or not self.values:
            raise ValueError("invalid_qdrant_filter_condition")


@dataclass(frozen=True, slots=True)
class ServerFilter:
    must: tuple[FilterCondition, ...]

    def __post_init__(self) -> None:
        if not self.must:
            raise ValueError("vector_scope_required")


@dataclass(frozen=True, slots=True)
class ClientAvailability:
    status: str
    reason: str


@runtime_checkable
class QdrantClientPort(Protocol):
    def probe(self) -> ClientAvailability: ...

    def collection_info(self, collection_name: str) -> ClientCollectionInfo | None: ...

    def create_collection(self, collection_name: str, *, dimensions: int, distance: str) -> None: ...

    def delete_collection(self, collection_name: str) -> None: ...

    def list_collections(self, *, prefix: str = "") -> tuple[str, ...]: ...

    def resolve_alias(self, alias_name: str) -> str | None: ...

    def swap_alias(self, alias_name: str, collection_name: str) -> None: ...

    def upsert(self, collection_name: str, points: Sequence[ClientPoint]) -> None: ...

    def retrieve(self, collection_name: str, point_ids: Sequence[str]) -> tuple[ClientPoint, ...]: ...

    def query_points(
        self,
        collection_name: str,
        *,
        query_vector: Sequence[float],
        query_filter: ServerFilter,
        limit: int,
    ) -> tuple[ClientScoredPoint, ...]: ...

    def delete_points(self, collection_name: str, point_ids: Sequence[str]) -> None: ...

    def delete_by_filter(self, collection_name: str, query_filter: ServerFilter) -> None: ...

    def close(self) -> None: ...


def normalise_origin(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https", "grpc", "grpcs"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise QdrantClientError(INVALID_ORIGIN, operation="validate_endpoint")
    try:
        port = parsed.port
    except ValueError as exc:
        raise QdrantClientError(INVALID_ORIGIN, operation="validate_endpoint") from exc
    if port is None:
        port = {
            "http": 80,
            "https": 443,
            "grpc": 6334,
            "grpcs": 6334,
        }[parsed.scheme]
    host = parsed.hostname.lower()
    rendered_host = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{rendered_host}:{port}"


def _is_loopback(origin: str) -> bool:
    hostname = str(urlsplit(origin).hostname or "").lower()
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_endpoint_policy(endpoint: Any) -> tuple[str, str | None]:
    rest_origin = normalise_origin(str(getattr(endpoint, "rest_url", "") or ""))
    raw_grpc = str(getattr(endpoint, "grpc_url", "") or "").strip()
    grpc_origin = normalise_origin(raw_grpc) if raw_grpc else None
    allowed = {
        normalise_origin(str(item))
        for item in tuple(getattr(endpoint, "allowed_origins", ()) or ())
    }
    try:
        trusted_private = frozenset(
            normalize_trusted_private_origins(
                tuple(
                    getattr(endpoint, "trusted_private_origins", ()) or ()
                ),
                allowed_origins=tuple(allowed),
            )
        )
    except VectorStoreEndpointPolicyError as exc:
        raise QdrantClientError(
            INVALID_ORIGIN,
            operation="validate_endpoint",
        ) from exc
    for origin in (rest_origin, grpc_origin):
        if origin is None:
            continue
        if origin not in allowed:
            raise QdrantClientError(INVALID_ORIGIN, operation="validate_endpoint")
        trusted = origin in trusted_private
        if (
            not _is_loopback(origin)
            and not trusted
            and not bool(getattr(endpoint, "external_calls_allowed", False))
        ):
            raise QdrantClientError(INVALID_ORIGIN, operation="validate_endpoint")
        secure = origin.startswith("https://") or origin.startswith("grpcs://")
        if not _is_loopback(origin) and (
            not secure or not bool(getattr(endpoint, "tls_verify", True))
        ):
            raise QdrantClientError(TLS_POLICY_VIOLATION, operation="validate_endpoint")
    return rest_origin, grpc_origin


def _classify_error(exc: BaseException, *, operation: str) -> QdrantClientError:
    name = type(exc).__name__.lower()
    status_code = getattr(exc, "status_code", None)
    if status_code in {401, 403} or "unauthor" in name or "forbidden" in name:
        return QdrantClientError(QDRANT_UNAUTHORIZED, operation=operation)
    if status_code == 404 or "notfound" in name or "not_found" in name:
        return QdrantClientError(COLLECTION_MISSING, operation=operation)
    if isinstance(exc, TimeoutError) or "timeout" in name:
        return QdrantClientError(QDRANT_TIMEOUT, operation=operation, retryable=True)
    return QdrantClientError(QDRANT_UNAVAILABLE, operation=operation, retryable=True)


def _load_qdrant_modules() -> tuple[Any, Any]:
    try:
        client_module = importlib.import_module("qdrant_client")
        models_module = importlib.import_module("qdrant_client.models")
    except (ImportError, ModuleNotFoundError) as exc:
        raise QdrantExtraRequiredError() from exc
    return client_module, models_module


def _configure_distinct_grpc_origin(
    raw_client: Any,
    *,
    rest_origin: str,
    grpc_origin: str | None,
) -> None:
    """Bind qdrant-client 1.18's gRPC channel to its independently approved origin.

    The pinned client exposes one public host argument for both transports. Its
    REST client is already fully constructed at this point, so changing the
    remote's gRPC channel fields before a channel exists leaves REST bound to
    ``rest_origin`` while honoring the separately allowlisted gRPC host.
    """

    if grpc_origin is None:
        return
    grpc = urlsplit(grpc_origin)
    remote = getattr(raw_client, "_client", None)
    required = ("_host", "_grpc_port", "_https", "_grpc_channel_pool")
    if remote is None or any(not hasattr(remote, name) for name in required):
        raise QdrantClientError(
            "qdrant_distinct_grpc_origin_unsupported",
            operation="configure_client",
        )
    if tuple(getattr(remote, "_grpc_channel_pool", ()) or ()):
        raise QdrantClientError(
            "qdrant_grpc_channel_already_initialized",
            operation="configure_client",
        )
    if grpc.hostname is None or grpc.port is None:
        raise QdrantClientError(INVALID_ORIGIN, operation="configure_client")
    remote._host = grpc.hostname
    remote._grpc_port = int(grpc.port)
    remote._https = grpc.scheme == "grpcs"
    # Keep an explicit audit-friendly transport record without exposing keys.
    remote._ananta_rest_origin = rest_origin
    remote._ananta_grpc_origin = grpc_origin


def _grpc_connect_timeout_options(
    connect_timeout_seconds: float,
    *,
    root_certificates: bytes | None = None,
) -> dict[str, Any]:
    """Bound gRPC reconnect backoff by the configured connection budget."""

    connect_timeout_ms = max(1, math.ceil(connect_timeout_seconds * 1000.0))
    initial_backoff_ms = min(1000, connect_timeout_ms)
    options: dict[str, Any] = {
        "grpc.initial_reconnect_backoff_ms": initial_backoff_ms,
        "grpc.min_reconnect_backoff_ms": initial_backoff_ms,
        "grpc.max_reconnect_backoff_ms": connect_timeout_ms,
    }
    if root_certificates is not None:
        options["root_certificates"] = root_certificates
    return options


def _configure_pinned_transport_timeouts(
    raw_client: Any,
    *,
    connect_timeout_seconds: float,
    request_timeout_seconds: float,
) -> None:
    """Apply split timeouts to qdrant-client 1.18's constructed transports."""

    remote = getattr(raw_client, "_client", None)
    openapi_client = getattr(remote, "openapi_client", None)
    api_client = getattr(openapi_client, "client", None)
    rest_client = getattr(api_client, "_client", None)
    if (
        remote is None
        or rest_client is None
        or not hasattr(rest_client, "timeout")
        or not hasattr(remote, "_timeout")
        or not hasattr(remote, "_rest_args")
    ):
        raise QdrantClientError(
            "qdrant_transport_timeout_unsupported",
            operation="configure_client",
        )
    try:
        httpx_module = importlib.import_module("httpx")
        rest_timeout = httpx_module.Timeout(
            connect=connect_timeout_seconds,
            read=request_timeout_seconds,
            write=request_timeout_seconds,
            pool=request_timeout_seconds,
        )
        rest_client.timeout = rest_timeout
    except (ImportError, ModuleNotFoundError, TypeError, ValueError) as exc:
        raise QdrantClientError(
            "qdrant_transport_timeout_unsupported",
            operation="configure_client",
        ) from exc
    remote._rest_args["timeout"] = rest_timeout
    # qdrant-client uses this value as the default deadline for gRPC RPCs.
    remote._timeout = request_timeout_seconds
    remote._ananta_rest_connect_timeout_seconds = connect_timeout_seconds
    remote._ananta_grpc_connect_timeout_seconds = connect_timeout_seconds
    remote._ananta_request_timeout_seconds = request_timeout_seconds


class QdrantClientAdapter:
    """The only production boundary that knows qdrant-client's API."""

    def __init__(
        self,
        *,
        rest_origin: str,
        grpc_origin: str | None = None,
        api_key: str | None = None,
        tls_ca_cert_pem: str | None = None,
        connect_timeout_seconds: float = 3.0,
        timeout_seconds: float = 10.0,
        prefer_grpc: bool = False,
        raw_client: Any | None = None,
        models_module: Any | None = None,
    ):
        connect_timeout = float(connect_timeout_seconds)
        timeout = float(timeout_seconds)
        if not 0.05 <= connect_timeout <= 300.0 or not 0.05 <= timeout <= 300.0:
            raise QdrantClientError("vector_store_invalid_timeout", operation="configure_client")
        self._rest_origin = normalise_origin(rest_origin)
        self._grpc_origin = normalise_origin(grpc_origin) if grpc_origin else None
        self._connect_timeout_seconds = connect_timeout
        self._timeout_seconds = timeout
        tls_ca_bytes: bytes | None = None
        rest_verify: ssl.SSLContext | bool = True
        if tls_ca_cert_pem:
            try:
                tls_ca_bytes = str(tls_ca_cert_pem).encode("utf-8")
                rest_verify = ssl.create_default_context()
                rest_verify.load_verify_locations(
                    cadata=str(tls_ca_cert_pem),
                )
            except (OSError, UnicodeError, ValueError, ssl.SSLError):
                raise QdrantClientError(
                    "vector_store_tls_ca_cert_invalid",
                    operation="configure_client",
                ) from None
        if raw_client is None:
            client_module, loaded_models = _load_qdrant_modules()
            grpc_port = urlsplit(self._grpc_origin).port if self._grpc_origin else None
            try:
                raw_client = client_module.QdrantClient(
                    url=self._rest_origin,
                    grpc_port=grpc_port,
                    api_key=api_key or None,
                    timeout=timeout,
                    prefer_grpc=bool(prefer_grpc),
                    verify=rest_verify,
                    grpc_options=_grpc_connect_timeout_options(
                        connect_timeout,
                        root_certificates=tls_ca_bytes,
                    ),
                    check_compatibility=False,
                )
                _configure_distinct_grpc_origin(
                    raw_client,
                    rest_origin=self._rest_origin,
                    grpc_origin=self._grpc_origin,
                )
                _configure_pinned_transport_timeouts(
                    raw_client,
                    connect_timeout_seconds=connect_timeout,
                    request_timeout_seconds=timeout,
                )
            except QdrantClientError:
                close = getattr(raw_client, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
                raise
            except Exception as exc:
                close = getattr(raw_client, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
                raise _classify_error(
                    exc,
                    operation="configure_client",
                ) from None
            models_module = loaded_models
        if models_module is None:
            raise TypeError("models_module is required with an injected raw_client")
        self._client = raw_client
        self._models = models_module

    @classmethod
    def from_endpoint(
        cls,
        endpoint: Any,
        *,
        api_key: str | None = None,
        tls_ca_cert_pem: str | None = None,
        raw_client: Any | None = None,
        models_module: Any | None = None,
    ) -> "QdrantClientAdapter":
        rest_origin, grpc_origin = validate_endpoint_policy(endpoint)
        return cls(
            rest_origin=rest_origin,
            grpc_origin=grpc_origin,
            api_key=api_key,
            tls_ca_cert_pem=tls_ca_cert_pem,
            connect_timeout_seconds=float(
                getattr(endpoint, "connect_timeout_seconds", 3.0)
            ),
            timeout_seconds=float(
                getattr(
                    endpoint,
                    "request_timeout_seconds",
                    getattr(endpoint, "timeout_seconds", 10.0),
                )
            ),
            prefer_grpc=bool(getattr(endpoint, "prefer_grpc", False)),
            raw_client=raw_client,
            models_module=models_module,
        )

    def _invoke(self, operation: str, callback: Any) -> Any:
        try:
            return callback()
        except QdrantClientError:
            raise
        except Exception as exc:
            raise _classify_error(exc, operation=operation) from None

    def probe(self) -> ClientAvailability:
        try:
            self._invoke("health", self._client.get_collections)
        except QdrantClientError as exc:
            status = "unauthorized" if exc.reason == QDRANT_UNAUTHORIZED else "unavailable"
            return ClientAvailability(status=status, reason=exc.reason)
        return ClientAvailability(status="ready", reason="ok")

    @staticmethod
    def _distance_text(value: Any) -> str:
        return str(getattr(value, "value", value) or "").lower()

    def collection_info(self, collection_name: str) -> ClientCollectionInfo | None:
        try:
            info = self._invoke(
                "collection_info",
                lambda: self._client.get_collection(collection_name=collection_name),
            )
        except QdrantClientError as exc:
            if exc.reason == COLLECTION_MISSING:
                return None
            raise
        vectors = getattr(getattr(getattr(info, "config", None), "params", None), "vectors", None)
        if isinstance(vectors, Mapping):
            vectors = next(iter(vectors.values()), None)
        return ClientCollectionInfo(
            name=collection_name,
            dimensions=int(getattr(vectors, "size", 0) or 0),
            distance=self._distance_text(getattr(vectors, "distance", "")),
            points_count=int(getattr(info, "points_count", 0) or 0),
        )

    def _distance_model(self, distance: str) -> Any:
        name = str(distance or "").lower()
        mapping = {
            "cosine": "COSINE",
            "dot": "DOT",
            "euclid": "EUCLID",
            "manhattan": "MANHATTAN",
        }
        if name not in mapping:
            raise QdrantClientError("vector_store_invalid_distance", operation="create_collection")
        return getattr(self._models.Distance, mapping[name])

    def create_collection(self, collection_name: str, *, dimensions: int, distance: str) -> None:
        vector_params = self._models.VectorParams(
            size=int(dimensions),
            distance=self._distance_model(distance),
        )
        self._invoke(
            "create_collection",
            lambda: self._client.create_collection(
                collection_name=collection_name,
                vectors_config=vector_params,
            ),
        )

    def delete_collection(self, collection_name: str) -> None:
        self._invoke(
            "delete_collection",
            lambda: self._client.delete_collection(collection_name=collection_name),
        )

    def list_collections(self, *, prefix: str = "") -> tuple[str, ...]:
        response = self._invoke("list_collections", self._client.get_collections)
        names = sorted(
            str(getattr(item, "name", "") or "")
            for item in tuple(getattr(response, "collections", ()) or ())
            if str(getattr(item, "name", "") or "").startswith(prefix)
        )
        return tuple(names)

    def resolve_alias(self, alias_name: str) -> str | None:
        response = self._invoke("resolve_alias", self._client.get_aliases)
        for item in tuple(getattr(response, "aliases", ()) or ()):
            if str(getattr(item, "alias_name", "") or "") == alias_name:
                return str(getattr(item, "collection_name", "") or "") or None
        return None

    def swap_alias(self, alias_name: str, collection_name: str) -> None:
        operations: list[Any] = []
        if self.resolve_alias(alias_name) is not None:
            operations.append(
                self._models.DeleteAliasOperation(
                    delete_alias=self._models.DeleteAlias(alias_name=alias_name)
                )
            )
        operations.append(
            self._models.CreateAliasOperation(
                create_alias=self._models.CreateAlias(
                    collection_name=collection_name,
                    alias_name=alias_name,
                )
            )
        )
        self._invoke(
            "swap_alias",
            lambda: self._client.update_collection_aliases(
                change_aliases_operations=operations
            ),
        )

    def upsert(self, collection_name: str, points: Sequence[ClientPoint]) -> None:
        structs = [
            self._models.PointStruct(
                id=point.point_id,
                vector=list(point.vector),
                payload=dict(point.payload),
            )
            for point in points
        ]
        if not structs:
            return
        self._invoke(
            "upsert",
            lambda: self._client.upsert(
                collection_name=collection_name,
                points=structs,
                wait=True,
            ),
        )

    def retrieve(self, collection_name: str, point_ids: Sequence[str]) -> tuple[ClientPoint, ...]:
        if not point_ids:
            return ()
        response = self._invoke(
            "retrieve",
            lambda: self._client.retrieve(
                collection_name=collection_name,
                ids=list(point_ids),
                with_payload=True,
                with_vectors=True,
            ),
        )
        return tuple(
            ClientPoint(
                point_id=str(getattr(item, "id", "") or ""),
                vector=tuple(float(value) for value in list(getattr(item, "vector", ()) or ())),
                payload=dict(getattr(item, "payload", {}) or {}),
            )
            for item in tuple(response or ())
        )

    def _to_filter(self, query_filter: ServerFilter) -> Any:
        conditions: list[Any] = []
        for condition in query_filter.must:
            if condition.match == "any":
                match = self._models.MatchAny(any=list(condition.values))
            else:
                match = self._models.MatchValue(value=condition.values[0])
            conditions.append(self._models.FieldCondition(key=condition.key, match=match))
        return self._models.Filter(must=conditions)

    def query_points(
        self,
        collection_name: str,
        *,
        query_vector: Sequence[float],
        query_filter: ServerFilter,
        limit: int,
    ) -> tuple[ClientScoredPoint, ...]:
        response = self._invoke(
            "query_points",
            lambda: self._client.query_points(
                collection_name=collection_name,
                query=list(query_vector),
                query_filter=self._to_filter(query_filter),
                limit=int(limit),
                with_payload=True,
                with_vectors=False,
            ),
        )
        return tuple(
            ClientScoredPoint(
                point_id=str(getattr(item, "id", "") or ""),
                score=float(getattr(item, "score", 0.0) or 0.0),
                payload=dict(getattr(item, "payload", {}) or {}),
            )
            for item in tuple(getattr(response, "points", ()) or ())
        )

    def delete_points(self, collection_name: str, point_ids: Sequence[str]) -> None:
        if not point_ids:
            return
        selector = self._models.PointIdsList(points=list(point_ids))
        self._invoke(
            "delete_points",
            lambda: self._client.delete(
                collection_name=collection_name,
                points_selector=selector,
                wait=True,
            ),
        )

    def delete_by_filter(self, collection_name: str, query_filter: ServerFilter) -> None:
        selector = self._models.FilterSelector(filter=self._to_filter(query_filter))
        self._invoke(
            "delete_by_filter",
            lambda: self._client.delete(
                collection_name=collection_name,
                points_selector=selector,
                wait=True,
            ),
        )

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            self._invoke("close", close)
