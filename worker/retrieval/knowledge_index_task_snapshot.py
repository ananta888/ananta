"""Worker-side loading of one Hub-authoritative index base task."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from urllib.parse import quote

from ananta_contracts.knowledge_index_dispatch import (
    parse_knowledge_index_dispatch,
)
from ananta_contracts.knowledge_index_task_snapshot import (
    MAX_KNOWLEDGE_INDEX_TASK_SNAPSHOT_BYTES,
    KnowledgeIndexTaskSnapshot,
    parse_knowledge_index_task_snapshot,
)

_MAX_RESPONSE_BYTES = MAX_KNOWLEDGE_INDEX_TASK_SNAPSHOT_BYTES + 4096


class KnowledgeIndexTaskSnapshotTransportPort(Protocol):
    def fetch(self, *, task_id: str) -> Mapping[str, Any]: ...


class KnowledgeIndexTaskSnapshotRepositoryPort(Protocol):
    def upsert_bound_knowledge_index_worker_snapshot(
        self,
        task_id: str,
        *,
        status: str,
        base_envelope: dict[str, Any],
        worker_binding: dict[str, Any],
    ) -> Any: ...


class HubKnowledgeIndexTaskSnapshotClient:
    """Bounded Worker-authenticated GET; redirects are never followed."""

    def __init__(
        self,
        *,
        hub_url: str,
        worker_id: str,
        worker_url: str,
        token_provider: Callable[[], str | None],
        timeout_seconds: float = 10.0,
        get: Callable[..., Any] | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._hub_url = str(hub_url or "").strip().rstrip("/")
        self._worker_id = str(worker_id or "").strip()
        self._worker_url = str(worker_url or "").strip().rstrip("/")
        if (
            not self._hub_url.startswith(("http://", "https://"))
            or not self._worker_id
            or not self._worker_url.startswith(("http://", "https://"))
        ):
            raise RuntimeError("knowledge_index_task_snapshot_identity_invalid")
        self._token_provider = token_provider
        self._timeout_seconds = max(
            1.0,
            min(float(timeout_seconds), 30.0),
        )
        self._get = get
        self._monotonic_clock = monotonic_clock
        self._requires_deadline_capable_transport = get is None

    def fetch(self, *, task_id: str) -> Mapping[str, Any]:
        deadline_monotonic = (
            float(self._monotonic_clock()) + self._timeout_seconds
        )
        token = str(self._token_provider() or "").strip()
        if len(token.encode("utf-8")) < 32:
            raise RuntimeError("knowledge_index_task_snapshot_worker_identity_unavailable")
        get = self._get
        if get is None:
            import requests

            get = requests.get
        response = None
        try:
            remaining = self._require_remaining(deadline_monotonic)
            response = get(
                self._hub_url
                + "/internal/tasks/"
                + quote(str(task_id or "").strip(), safe="")
                + "/knowledge-index-base-snapshot",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Ananta-Worker-ID": self._worker_id,
                    "X-Ananta-Worker-URL": self._worker_url,
                    "Accept-Encoding": "identity",
                },
                timeout=(min(5.0, remaining), remaining),
                allow_redirects=False,
                stream=True,
            )
            self._require_remaining(deadline_monotonic)
            status_code = int(getattr(response, "status_code", 500) or 500)
            if 300 <= status_code < 400:
                raise RuntimeError("knowledge_index_task_snapshot_redirect_forbidden")
            body = self._bounded_json(
                response,
                deadline_monotonic=deadline_monotonic,
            )
            data = body.get("data") if isinstance(body, Mapping) else None
            if status_code >= 400:
                reason = (
                    str((data or {}).get("reason_code") if isinstance(data, Mapping) else "").strip()
                    or "knowledge_index_task_snapshot_denied"
                )
                raise RuntimeError(reason)
            if (
                status_code != 200
                or not isinstance(body, Mapping)
                or set(body) != {"status", "data"}
                or body.get("status") != "success"
                or not isinstance(data, Mapping)
            ):
                raise RuntimeError("knowledge_index_task_snapshot_response_invalid")
            return dict(data)
        except RuntimeError:
            raise
        except Exception as exc:
            if float(self._monotonic_clock()) >= deadline_monotonic:
                raise RuntimeError(
                    "knowledge_index_task_snapshot_deadline_exceeded"
                ) from exc
            raise RuntimeError("knowledge_index_task_snapshot_unavailable") from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def _bounded_json(
        self,
        response: Any,
        *,
        deadline_monotonic: float,
    ) -> Any:
        headers = getattr(response, "headers", None)
        declared = headers.get("Content-Length") if isinstance(headers, Mapping) else None
        try:
            self._require_remaining(deadline_monotonic)
            declared_length = int(declared) if declared is not None else None
            if declared_length is not None:
                if declared_length < 0:
                    raise RuntimeError("knowledge_index_task_snapshot_response_invalid")
                if declared_length > _MAX_RESPONSE_BYTES:
                    raise RuntimeError(
                        "knowledge_index_task_snapshot_response_too_large"
                    )
            chunks: list[bytes] = []
            total = 0
            for chunk in self._response_chunks(
                response,
                deadline_monotonic=deadline_monotonic,
                declared_length=declared_length,
            ):
                if not isinstance(chunk, bytes):
                    raise RuntimeError("knowledge_index_task_snapshot_response_invalid")
                total += len(chunk)
                if total > _MAX_RESPONSE_BYTES:
                    raise RuntimeError("knowledge_index_task_snapshot_response_too_large")
                chunks.append(chunk)
            self._require_remaining(deadline_monotonic)
            return json.loads(b"".join(chunks))
        except RuntimeError:
            raise
        except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
            raise RuntimeError("knowledge_index_task_snapshot_response_invalid") from exc

    def _response_chunks(
        self,
        response: Any,
        *,
        deadline_monotonic: float,
        declared_length: int | None,
    ):
        """Yield bounded reads while shrinking the real socket deadline.

        Production requests responses are read through http.client.read1 so a
        peer cannot reset an inactivity timeout forever by drip-feeding a large
        chunk. Injected test transports remain supported and are governed by
        the same monotonic checks around each yielded chunk.
        """

        if self._requires_deadline_capable_transport:
            encoding = ""
            headers = getattr(response, "headers", None)
            if isinstance(headers, Mapping):
                encoding = str(headers.get("Content-Encoding") or "").strip().lower()
            if encoding not in {"", "identity"}:
                raise RuntimeError(
                    "knowledge_index_task_snapshot_content_encoding_forbidden"
                )
            raw = getattr(response, "raw", None)
            http_response = getattr(raw, "_fp", None)
            reader = getattr(http_response, "read1", None)
            if not callable(reader):
                raise RuntimeError(
                    "knowledge_index_task_snapshot_deadline_transport_unsupported"
                )
            # Reading through ``_fp`` bypasses urllib3's
            # ``length_remaining`` bookkeeping. Count the bytes here so a
            # response that closes exactly at Content-Length is still proven
            # complete without requiring one more socket operation.
            bytes_read = 0
            while True:
                remaining = self._require_remaining(deadline_monotonic)
                if declared_length is not None and bytes_read == declared_length:
                    return
                if declared_length is None and self._response_is_complete(response):
                    return
                if not self._set_socket_timeout(response, remaining):
                    raise RuntimeError(
                        "knowledge_index_task_snapshot_deadline_transport_unsupported"
                    )
                read_size = (
                    min(65_536, declared_length - bytes_read)
                    if declared_length is not None
                    else 65_536
                )
                chunk = reader(read_size)
                self._require_remaining(deadline_monotonic)
                if not chunk:
                    if declared_length is not None and bytes_read != declared_length:
                        raise RuntimeError(
                            "knowledge_index_task_snapshot_response_invalid"
                        )
                    return
                bytes_read += len(chunk)
                if declared_length is not None and bytes_read > declared_length:
                    raise RuntimeError(
                        "knowledge_index_task_snapshot_response_invalid"
                    )
                yield chunk
            return

        iterator = getattr(response, "iter_content", None)
        values = (
            iterator(chunk_size=65_536, decode_unicode=False)
            if callable(iterator)
            else iter((getattr(response, "content", b""),))
        )
        for chunk in values:
            self._require_remaining(deadline_monotonic)
            yield chunk
            self._require_remaining(deadline_monotonic)

    def _require_remaining(self, deadline_monotonic: float) -> float:
        remaining = deadline_monotonic - float(self._monotonic_clock())
        if remaining <= 0:
            raise RuntimeError(
                "knowledge_index_task_snapshot_deadline_exceeded"
            )
        return remaining

    @staticmethod
    def _set_socket_timeout(response: Any, timeout: float) -> bool:
        raw = getattr(response, "raw", None)
        http_response = getattr(raw, "_fp", None)
        buffered = getattr(http_response, "fp", None)
        socket_raw = getattr(buffered, "raw", None)
        connection = getattr(raw, "_connection", None)
        for candidate in (
            getattr(socket_raw, "_sock", None),
            getattr(connection, "sock", None),
            socket_raw,
            buffered,
        ):
            setter = getattr(candidate, "settimeout", None)
            if callable(setter):
                setter(timeout)
                return True
        return False

    @staticmethod
    def _response_is_complete(response: Any) -> bool:
        raw = getattr(response, "raw", None)
        http_response = getattr(raw, "_fp", None)
        if getattr(raw, "length_remaining", None) == 0:
            return True
        for candidate in (raw, http_response):
            isclosed = getattr(candidate, "isclosed", None)
            if callable(isclosed):
                try:
                    if bool(isclosed()):
                        return True
                except (AttributeError, OSError, TypeError, ValueError):
                    pass
            if getattr(candidate, "closed", False) is True:
                return True
        return False


class KnowledgeIndexTaskSnapshotLoader:
    """Validate one snapshot and persist it before normal task lookup."""

    def __init__(
        self,
        *,
        transport: KnowledgeIndexTaskSnapshotTransportPort,
        task_repository: KnowledgeIndexTaskSnapshotRepositoryPort,
        worker_id: str,
        worker_url: str,
        clock_ms=lambda: int(time.time() * 1000),
    ) -> None:
        self._transport = transport
        self._tasks = task_repository
        self._worker_id = str(worker_id or "").strip()
        self._worker_url = str(worker_url or "").strip().rstrip("/")
        self._clock_ms = clock_ms
        if not self._worker_id or not self._worker_url:
            raise RuntimeError("knowledge_index_task_snapshot_worker_identity_invalid")

    def ensure_for_dispatch(
        self,
        *,
        task_id: str,
        raw_marker: Mapping[str, Any],
        expected_phase: str,
    ) -> KnowledgeIndexTaskSnapshot:
        marker = parse_knowledge_index_dispatch(
            raw_marker,
            expected_phase=expected_phase,
            expected_job_id=task_id,
        )
        snapshot = parse_knowledge_index_task_snapshot(
            self._transport.fetch(task_id=marker.job_id),
            expected_job_id=marker.job_id,
            expected_worker_id=self._worker_id,
            expected_worker_url=self._worker_url,
            now_epoch_ms=int(self._clock_ms()),
        )
        if marker.source_access_manifest is not None:
            manifest = marker.source_access_manifest
            assignment = dict(snapshot.job.get("assignment") or {})
            if str(manifest.get("assignment_id") or "") != str(assignment.get("assignment_id") or "") or str(
                manifest.get("lease_id") or ""
            ) != str(assignment.get("lease_id") or ""):
                raise ValueError("knowledge_index_task_snapshot_manifest_mismatch")
        self._tasks.upsert_bound_knowledge_index_worker_snapshot(
            snapshot.task_id,
            status=snapshot.status,
            base_envelope=dict(snapshot.job),
            worker_binding={
                "schema": "ananta.knowledge_index_worker_binding.v1",
                "worker_id": snapshot.worker_id,
                "worker_url": snapshot.worker_url,
            },
        )
        return snapshot


_LOADER: KnowledgeIndexTaskSnapshotLoader | None = None
_LOADER_LOCK = threading.Lock()


def get_knowledge_index_task_snapshot_loader() -> KnowledgeIndexTaskSnapshotLoader:
    global _LOADER
    with _LOADER_LOCK:
        if _LOADER is None:
            from agent.auth import resolve_configured_agent_token
            from agent.config import settings
            from agent.services.repository_registry import (
                get_repository_registry,
            )

            worker_url = str(settings.agent_url or f"http://localhost:{settings.port}").strip().rstrip("/")
            transport = HubKnowledgeIndexTaskSnapshotClient(
                hub_url=str(settings.hub_url or ""),
                worker_id=str(settings.agent_name or ""),
                worker_url=worker_url,
                token_provider=resolve_configured_agent_token,
            )
            _LOADER = KnowledgeIndexTaskSnapshotLoader(
                transport=transport,
                task_repository=get_repository_registry().task_repo,
                worker_id=str(settings.agent_name or ""),
                worker_url=worker_url,
            )
        return _LOADER


def hydrate_knowledge_index_task_snapshot(
    *,
    task_id: str,
    request_data: Any,
    expected_phase: str,
) -> KnowledgeIndexTaskSnapshot | None:
    from agent.config import settings

    if str(settings.role or "").strip().lower() != "worker":
        return None
    raw_marker = getattr(
        request_data,
        "knowledge_index_dispatch",
        None,
    )
    if not isinstance(raw_marker, Mapping):
        return None
    return get_knowledge_index_task_snapshot_loader().ensure_for_dispatch(
        task_id=task_id,
        raw_marker=raw_marker,
        expected_phase=expected_phase,
    )


__all__ = [
    "HubKnowledgeIndexTaskSnapshotClient",
    "KnowledgeIndexTaskSnapshotLoader",
    "KnowledgeIndexTaskSnapshotRepositoryPort",
    "KnowledgeIndexTaskSnapshotTransportPort",
    "get_knowledge_index_task_snapshot_loader",
    "hydrate_knowledge_index_task_snapshot",
]
