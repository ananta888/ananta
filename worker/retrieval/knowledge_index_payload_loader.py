"""Bounded retrieval of Hub-owned knowledge-index payload artifacts."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from worker.retrieval.knowledge_index_execution_guard import (
    KnowledgeIndexExecutionDeadlineError,
    KnowledgeIndexExecutionDeadlinePort,
)
from worker.retrieval.knowledge_index_job_contract import MAX_PAYLOAD_BYTES

_PAYLOAD_READ_CHUNK_BYTES = 1024 * 1024


class _KnowledgeIndexPayloadNoRedirectHandler(
    urllib.request.HTTPRedirectHandler
):
    """Keep a delegated payload capability on the configured Hub request."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class HubArtifactKnowledgeIndexPayloadLoader:
    """Fetch a Hub-owned payload artifact with strict size and digest bounds."""

    def load(self, reference: Mapping[str, Any]) -> bytes:
        artifact_id = str(reference.get("artifact_id") or "").strip()
        expected_size = int(reference.get("size_bytes") or -1)
        if not artifact_id or expected_size < 0 or expected_size > MAX_PAYLOAD_BYTES:
            raise ValueError("knowledge_index_payload_artifact_ref_invalid")
        local = self._load_local(artifact_id, expected_size=expected_size)
        if local is not None:
            return local
        return self._load_from_hub(
            artifact_id,
            expected_size=expected_size,
            expected_sha256=str(reference.get("sha256") or "").lower(),
        )

    def load_authorized(
        self,
        reference: Mapping[str, Any],
        *,
        source_access_manifest: Mapping[str, Any],
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> bytes:
        artifact_id = str(reference.get("artifact_id") or "").strip()
        expected_size = int(reference.get("size_bytes") or -1)
        if not artifact_id or expected_size < 0 or expected_size > MAX_PAYLOAD_BYTES:
            raise ValueError("knowledge_index_payload_artifact_ref_invalid")
        if not isinstance(source_access_manifest, Mapping):
            raise ValueError(
                "knowledge_index_payload_capability_required"
            )
        # Governed reads always return through the Hub so the signed,
        # assignment-bound capability is revalidated against live authority.
        # A shared filesystem must never bypass that control-plane decision.
        hub_load_kwargs: dict[str, Any] = {
            "expected_size": expected_size,
            "expected_sha256": str(reference.get("sha256") or "").lower(),
            "source_access_manifest": source_access_manifest,
        }
        if execution_deadline is not None:
            hub_load_kwargs["execution_deadline"] = execution_deadline
        return self._load_from_hub(artifact_id, **hub_load_kwargs)

    @staticmethod
    def _load_local(artifact_id: str, *, expected_size: int) -> bytes | None:
        try:
            from agent.repository import artifact_version_repo

            versions = artifact_version_repo.get_by_artifact(artifact_id)
            if not versions:
                return None
            path = Path(str(versions[0].storage_path)).resolve(strict=True)
            if not path.is_file() or path.stat().st_size != expected_size:
                return None
            return path.read_bytes()
        except (OSError, ValueError):
            return None

    @staticmethod
    def _load_from_hub(
        artifact_id: str,
        *,
        expected_size: int,
        expected_sha256: str,
        source_access_manifest: Mapping[str, Any] | None = None,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None = None,
    ) -> bytes:
        from agent.auth import resolve_configured_agent_token
        from agent.config import settings
        from worker.runtime.workflow_service_identity import WorkflowServiceIdentity

        hub_url = str(settings.hub_url or "").strip().rstrip("/")
        if not hub_url.startswith(("http://", "https://")):
            raise ValueError("knowledge_index_payload_hub_url_invalid")
        token = resolve_configured_agent_token(
            {
                "AGENT_TOKEN": settings.agent_token,
                "AGENT_TOKEN_FILE": settings.agent_token_file,
            }
        )
        if source_access_manifest is not None and not token:
            raise ValueError(
                "knowledge_index_payload_worker_service_token_required"
            )
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        identity = WorkflowServiceIdentity.optional(
            worker_id=settings.agent_name,
            worker_url=str(settings.agent_url or ""),
        )
        if identity is not None:
            headers.update(identity.headers())
        if source_access_manifest is not None:
            from ananta_contracts.knowledge_index_payload_capability import (
                KNOWLEDGE_INDEX_PAYLOAD_CAPABILITY_HEADER,
                encode_knowledge_index_payload_capability,
            )

            headers[KNOWLEDGE_INDEX_PAYLOAD_CAPABILITY_HEADER] = (
                encode_knowledge_index_payload_capability(
                    source_access_manifest
                )
            )
        encoded_id = urllib.parse.quote(artifact_id, safe="")
        request = urllib.request.Request(
            f"{hub_url}/internal/knowledge-index/payload-artifacts/{encoded_id}",
            headers=headers,
            method="GET",
        )
        opener = urllib.request.build_opener(
            _KnowledgeIndexPayloadNoRedirectHandler()
        )
        timeout = 60.0
        if execution_deadline is not None:
            execution_deadline.checkpoint()
            timeout = min(
                timeout,
                execution_deadline.remaining_seconds(),
            )
        try:
            response = opener.open(request, timeout=timeout)
        except TimeoutError:
            if execution_deadline is not None:
                execution_deadline.checkpoint()
            raise
        except urllib.error.HTTPError as exc:
            if 300 <= int(exc.code or 0) < 400:
                exc.close()
                raise ValueError(
                    "knowledge_index_payload_redirect_forbidden"
                ) from exc
            raise
        status = getattr(response, "status", None)
        if status is None:
            getcode = getattr(response, "getcode", None)
            status = getcode() if callable(getcode) else None
        if status is not None and 300 <= int(status) < 400:
            response.close()
            raise ValueError(
                "knowledge_index_payload_redirect_forbidden"
            )
        with response:
            if execution_deadline is not None:
                execution_deadline.checkpoint()
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) != expected_size:
                raise ValueError("knowledge_index_payload_artifact_size_mismatch")
            declared_hash = str(response.headers.get("X-Artifact-SHA256") or "").lower()
            if declared_hash and declared_hash != expected_sha256:
                raise ValueError("knowledge_index_payload_artifact_digest_mismatch")
            chunks: list[bytes] = []
            received = 0
            while True:
                if declared is not None and received == expected_size:
                    break
                maximum = min(
                    _PAYLOAD_READ_CHUNK_BYTES,
                    expected_size - received + 1,
                )
                chunk = HubArtifactKnowledgeIndexPayloadLoader._read_chunk(
                    response,
                    maximum,
                    execution_deadline=execution_deadline,
                )
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > expected_size:
                    raise ValueError(
                        "knowledge_index_payload_artifact_size_mismatch"
                    )
            content = b"".join(chunks)
        if len(content) != expected_size:
            raise ValueError("knowledge_index_payload_artifact_size_mismatch")
        return content

    @staticmethod
    def _read_chunk(
        response: Any,
        maximum_bytes: int,
        *,
        execution_deadline: KnowledgeIndexExecutionDeadlinePort | None,
    ) -> bytes:
        if maximum_bytes <= 0:
            return b""
        if execution_deadline is not None:
            execution_deadline.checkpoint()
            if not HubArtifactKnowledgeIndexPayloadLoader._set_socket_timeout(
                response,
                min(60.0, execution_deadline.remaining_seconds()),
            ):
                raise KnowledgeIndexExecutionDeadlineError(
                    "knowledge_index_worker_payload_deadline_transport_unsupported"
                )
        reader = getattr(response, "read1", None)
        if not callable(reader):
            reader = response.read
        try:
            chunk = reader(maximum_bytes)
        except TimeoutError:
            if execution_deadline is not None:
                execution_deadline.checkpoint()
            raise
        if execution_deadline is not None:
            execution_deadline.checkpoint()
        if not isinstance(chunk, bytes):
            raise ValueError("knowledge_index_payload_artifact_bytes_invalid")
        return chunk

    @staticmethod
    def _set_socket_timeout(response: Any, timeout: float) -> bool:
        fp = getattr(response, "fp", None)
        raw = getattr(fp, "raw", None)
        for candidate in (getattr(raw, "_sock", None), raw, fp):
            setter = getattr(candidate, "settimeout", None)
            if callable(setter):
                setter(timeout)
                return True
        return False


__all__ = [
    "HubArtifactKnowledgeIndexPayloadLoader",
    "_KnowledgeIndexPayloadNoRedirectHandler",
]
