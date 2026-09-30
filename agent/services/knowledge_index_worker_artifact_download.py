"""Assignment-bound HTTP transport for Worker knowledge-index artifacts."""

from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.knowledge_index_worker_artifact_contract import (
    DOWNLOAD_CHUNK_BYTES,
    JOB_ID_PATTERN,
    MAX_ARTIFACT_BYTES,
)
from agent.services.knowledge_index_worker_artifact_ports import (
    KnowledgeIndexArtifactTransferDeadlinePort,
    KnowledgeIndexWorkerNoRedirectHandler,
)
from ananta_contracts.knowledge_index_legacy_output_capability import (
    KNOWLEDGE_INDEX_LEGACY_OUTPUT_CAPABILITY_HEADER,
    encode_legacy_output_capability,
)
from ananta_contracts.knowledge_index_worker_output_capability import (
    KNOWLEDGE_INDEX_OUTPUT_CAPABILITY_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_INDEX_ID_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_JOB_ID_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_MEDIA_TYPE_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_ROLE_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_RUN_ID_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_SHA256_HEADER,
    KNOWLEDGE_INDEX_OUTPUT_SIZE_HEADER,
    encode_knowledge_index_output_capability,
)


class HttpKnowledgeIndexWorkerArtifactDownloader:
    """Download one bounded artifact from the assigned worker only."""

    supports_assignment_bound_legacy_transport = True

    def __init__(self, *, opener: Any | None = None) -> None:
        self._opener = opener or urllib.request.build_opener(
            KnowledgeIndexWorkerNoRedirectHandler()
        )

    def download(
        self,
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
        source_access_manifest: Mapping[str, Any] | None = None,
        job_id: str | None = None,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None = None,
    ) -> bytes:
        request, expected_size, expected_hash = self._request(
            worker_url=worker_url,
            worker_token=worker_token,
            reference=reference,
            source_access_manifest=source_access_manifest,
            job_id=job_id,
        )
        chunks: list[bytes] = []
        received = 0
        with self._open(request, transfer_deadline=transfer_deadline) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) != expected_size:
                raise ValueError("knowledge_index_worker_artifact_size_mismatch")
            while True:
                if declared is not None and received == expected_size:
                    break
                chunk = self._read_chunk(
                    response,
                    min(DOWNLOAD_CHUNK_BYTES, expected_size - received + 1),
                    transfer_deadline=transfer_deadline,
                )
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > expected_size:
                    raise ValueError(
                        "knowledge_index_worker_artifact_size_mismatch"
                    )
        content = b"".join(chunks)
        if len(content) != expected_size:
            raise ValueError("knowledge_index_worker_artifact_size_mismatch")
        if hashlib.sha256(content).hexdigest() != expected_hash:
            raise ValueError("knowledge_index_worker_artifact_digest_mismatch")
        return content

    def download_to_path(
        self,
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
        destination: Path,
        source_access_manifest: Mapping[str, Any] | None = None,
        job_id: str | None = None,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None = None,
    ) -> None:
        """Stream one verified worker artifact directly into Hub staging."""

        request, expected_size, expected_hash = self._request(
            worker_url=worker_url,
            worker_token=worker_token,
            reference=reference,
            source_access_manifest=source_access_manifest,
            job_id=job_id,
        )
        if destination.exists() or destination.is_symlink():
            raise ValueError("knowledge_index_worker_artifact_staging_conflict")
        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        hasher = hashlib.sha256()
        try:
            with self._open(
                request,
                transfer_deadline=transfer_deadline,
            ) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) != expected_size:
                    raise ValueError("knowledge_index_worker_artifact_size_mismatch")
                with destination.open("xb") as handle:
                    while True:
                        if declared is not None and written == expected_size:
                            break
                        chunk = self._read_chunk(
                            response,
                            min(
                                DOWNLOAD_CHUNK_BYTES,
                                expected_size - written + 1,
                            ),
                            transfer_deadline=transfer_deadline,
                        )
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > expected_size:
                            raise ValueError("knowledge_index_worker_artifact_size_mismatch")
                        hasher.update(chunk)
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
            if written != expected_size:
                raise ValueError("knowledge_index_worker_artifact_size_mismatch")
            if hasher.hexdigest() != expected_hash:
                raise ValueError("knowledge_index_worker_artifact_digest_mismatch")
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def _open(
        self,
        request: urllib.request.Request,
        *,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None,
    ) -> Any:
        """Open exactly one URL and reject every redirect response."""

        timeout = 60.0
        if transfer_deadline is not None:
            timeout = min(
                timeout,
                transfer_deadline.require_remaining_seconds(),
            )
        try:
            response = self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if 300 <= int(exc.code or 0) < 400:
                exc.close()
                raise ValueError(
                    "knowledge_index_worker_artifact_redirect_forbidden"
                ) from exc
            raise
        status = getattr(response, "status", None)
        if status is None:
            getcode = getattr(response, "getcode", None)
            status = getcode() if callable(getcode) else None
        if status is not None and 300 <= int(status) < 400:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            raise ValueError(
                "knowledge_index_worker_artifact_redirect_forbidden"
            )
        if transfer_deadline is not None:
            transfer_deadline.require_remaining_seconds()
        return response

    @staticmethod
    def _read_chunk(
        response: Any,
        maximum_bytes: int,
        *,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None,
    ) -> bytes:
        if maximum_bytes <= 0:
            return b""
        if transfer_deadline is not None:
            remaining = transfer_deadline.require_remaining_seconds()
            if not HttpKnowledgeIndexWorkerArtifactDownloader._set_socket_timeout(
                response,
                min(60.0, remaining),
            ):
                raise ValueError(
                    "knowledge_index_worker_artifact_deadline_transport_unsupported"
                )
        reader = getattr(response, "read1", None)
        if not callable(reader):
            reader = response.read
        try:
            chunk = reader(maximum_bytes)
        except TimeoutError:
            if transfer_deadline is not None:
                # Preserve the typed absolute-deadline outcome when the
                # narrowed socket timeout and the shared clock expire together.
                transfer_deadline.require_remaining_seconds()
            raise
        if transfer_deadline is not None:
            transfer_deadline.require_remaining_seconds()
        if not isinstance(chunk, bytes):
            raise ValueError("knowledge_index_worker_artifact_bytes_invalid")
        return chunk

    @staticmethod
    def _set_socket_timeout(response: Any, timeout: float) -> bool:
        """Narrow the production urllib socket before each single raw read."""

        fp = getattr(response, "fp", None)
        raw = getattr(fp, "raw", None)
        candidates = (
            getattr(raw, "_sock", None),
            raw,
            fp,
        )
        for candidate in candidates:
            setter = getattr(candidate, "settimeout", None)
            if callable(setter):
                setter(timeout)
                return True
        return False

    @staticmethod
    def _request(
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
        source_access_manifest: Mapping[str, Any] | None = None,
        job_id: str | None = None,
    ) -> tuple[urllib.request.Request, int, str]:
        parsed = urllib.parse.urlsplit(str(worker_url or "").rstrip("/"))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("knowledge_index_worker_url_invalid")
        artifact_id = str(reference.get("artifact_id") or "").strip()
        raw_size = reference.get("size_bytes")
        if isinstance(raw_size, bool) or not isinstance(raw_size, int):
            raise ValueError("knowledge_index_worker_artifact_ref_invalid")
        expected_hash = str(reference.get("sha256") or "").lower()
        if not artifact_id or raw_size < 0 or raw_size > MAX_ARTIFACT_BYTES:
            raise ValueError("knowledge_index_worker_artifact_ref_invalid")
        if len(expected_hash) != 64 or any(
            char not in "0123456789abcdef" for char in expected_hash
        ):
            raise ValueError("knowledge_index_worker_artifact_digest_invalid")
        base_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
        )
        encoded_id = urllib.parse.quote(artifact_id, safe="")
        normalized_token = str(worker_token or "").strip()
        normalized_job_id = str(job_id or "").strip()
        internal_transport_requested = job_id is not None
        if internal_transport_requested:
            if (
                not JOB_ID_PATTERN.fullmatch(normalized_job_id)
            ):
                raise ValueError(
                    "knowledge_index_worker_artifact_transport_unavailable"
                )
            path = (
                "/internal/knowledge-index/output-artifacts/"
                f"{encoded_id}"
            )
            headers = {
                KNOWLEDGE_INDEX_OUTPUT_JOB_ID_HEADER: normalized_job_id,
                KNOWLEDGE_INDEX_OUTPUT_INDEX_ID_HEADER: str(
                    reference.get("knowledge_index_id") or ""
                ),
                KNOWLEDGE_INDEX_OUTPUT_RUN_ID_HEADER: str(
                    reference.get("run_id") or ""
                ),
                KNOWLEDGE_INDEX_OUTPUT_ROLE_HEADER: str(
                    reference.get("role") or ""
                ),
                KNOWLEDGE_INDEX_OUTPUT_SHA256_HEADER: expected_hash,
                KNOWLEDGE_INDEX_OUTPUT_SIZE_HEADER: str(raw_size),
                KNOWLEDGE_INDEX_OUTPUT_MEDIA_TYPE_HEADER: str(
                    reference.get("media_type") or ""
                ),
            }
            if source_access_manifest is not None:
                if not isinstance(source_access_manifest, Mapping):
                    raise ValueError(
                        "knowledge_index_worker_artifact_transport_unavailable"
                    )
                headers[KNOWLEDGE_INDEX_OUTPUT_CAPABILITY_HEADER] = (
                    encode_knowledge_index_output_capability(
                        source_access_manifest
                    )
                )
            elif normalized_token:
                legacy_binding = {
                    "artifact_id": artifact_id,
                    "job_id": normalized_job_id,
                    "knowledge_index_id": str(reference.get("knowledge_index_id") or ""),
                    "run_id": str(reference.get("run_id") or ""),
                    "output_role": str(reference.get("role") or ""),
                    "sha256": expected_hash,
                    "size_bytes": raw_size,
                    "media_type": str(reference.get("media_type") or ""),
                }
                headers[KNOWLEDGE_INDEX_LEGACY_OUTPUT_CAPABILITY_HEADER] = (
                    encode_legacy_output_capability(
                        legacy_binding, secret=normalized_token
                    )
                )
            if normalized_token:
                headers["Authorization"] = (
                    f"Bearer {normalized_token}"
                )
        elif normalized_token:
            path = f"/artifacts/{encoded_id}/content"
            headers = {"Authorization": f"Bearer {normalized_token}"}
        else:
            raise ValueError(
                "knowledge_index_worker_artifact_transport_unavailable"
            )
        return (
            urllib.request.Request(
                f"{base_url}{path}",
                headers=headers,
                method="GET",
            ),
            raw_size,
            expected_hash,
        )


__all__ = ["HttpKnowledgeIndexWorkerArtifactDownloader"]
