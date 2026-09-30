"""Hub staging and byte verification of downloaded Worker artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.services.knowledge_index_worker_artifact_contract import (
    DOWNLOAD_CHUNK_BYTES,
    MAX_GRAPH_JSON_BYTES,
    OUTPUT_FILENAMES,
)
from agent.services.knowledge_index_worker_artifact_ports import (
    KnowledgeIndexArtifactTransferDeadlinePort,
    KnowledgeIndexWorkerArtifactDownloaderPort,
    KnowledgeIndexWorkerStreamingArtifactDownloaderPort,
)


def file_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(DOWNLOAD_CHUNK_BYTES), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_downloaded_content(
    *,
    reference: Mapping[str, Any],
    content: bytes,
) -> None:
    raw_size = reference.get("size_bytes")
    if isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size < 0:
        raise ValueError("knowledge_index_worker_artifact_size_invalid")
    expected_size = raw_size
    expected_hash = str(reference.get("sha256") or "").lower()
    if len(content) != expected_size:
        raise ValueError("knowledge_index_worker_artifact_size_mismatch")
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError("knowledge_index_worker_artifact_digest_mismatch")


def verify_staged_file(
    *,
    reference: Mapping[str, Any],
    path: Path,
) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("knowledge_index_worker_artifact_staging_invalid")
    raw_size = reference.get("size_bytes")
    if isinstance(raw_size, bool) or not isinstance(raw_size, int):
        raise ValueError("knowledge_index_worker_artifact_size_invalid")
    if path.stat().st_size != raw_size:
        raise ValueError("knowledge_index_worker_artifact_size_mismatch")
    if file_sha256(path) != str(reference.get("sha256") or "").lower():
        raise ValueError("knowledge_index_worker_artifact_digest_mismatch")


def promote_staging(
    *,
    staging_dir: Path,
    output_dir: Path,
    staged_paths: Mapping[str, Path],
) -> None:
    if output_dir.exists():
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise ValueError("knowledge_index_worker_artifact_output_invalid")
        for role, staged_path in staged_paths.items():
            target = output_dir / OUTPUT_FILENAMES[role]
            if (
                target.is_symlink()
                or not target.is_file()
                or target.stat().st_size != staged_path.stat().st_size
                or file_sha256(target) != file_sha256(staged_path)
            ):
                raise ValueError("knowledge_index_worker_artifact_conflict")
        return
    os.replace(staging_dir, output_dir)


def strict_json_object(path: Path) -> dict[str, Any]:
    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite_json_number")

    if path.is_symlink() or not path.is_file():
        raise ValueError("knowledge_index_worker_graph_artifact_staging_invalid")
    with path.open("rb") as handle:
        content = handle.read(MAX_GRAPH_JSON_BYTES + 1)
    if len(content) > MAX_GRAPH_JSON_BYTES:
        raise ValueError("knowledge_index_worker_graph_artifact_too_large")
    try:
        payload = json.loads(content.decode("utf-8"), parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("knowledge_index_worker_graph_artifact_json_invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("knowledge_index_worker_graph_artifact_json_invalid")
    return dict(payload)


class KnowledgeIndexWorkerArtifactStager:
    """Download one reference into Hub staging and verify its bytes."""

    def __init__(
        self,
        *,
        downloader: (
            KnowledgeIndexWorkerArtifactDownloaderPort
            | KnowledgeIndexWorkerStreamingArtifactDownloaderPort
        ),
    ) -> None:
        self._downloader = downloader

    def _transport(
        self,
        *,
        worker_url: str,
        worker_token: str,
        source_access_manifest: Mapping[str, Any] | None,
        job_id: str,
        reference: Mapping[str, Any],
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None,
    ) -> dict[str, Any]:
        transport: dict[str, Any] = {
            "worker_url": worker_url,
            "worker_token": worker_token,
            "reference": reference,
        }
        if source_access_manifest is not None or bool(
            getattr(
                self._downloader,
                "supports_assignment_bound_legacy_transport",
                False,
            )
        ):
            transport["job_id"] = job_id
        if source_access_manifest is not None:
            transport["source_access_manifest"] = source_access_manifest
        if transfer_deadline is not None:
            transport["transfer_deadline"] = transfer_deadline
        return transport

    def stage_reference(
        self,
        *,
        worker_url: str,
        worker_token: str,
        source_access_manifest: Mapping[str, Any] | None,
        job_id: str,
        reference: Mapping[str, Any],
        destination: Path,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None,
    ) -> None:
        transport = self._transport(
            worker_url=worker_url,
            worker_token=worker_token,
            source_access_manifest=source_access_manifest,
            job_id=job_id,
            reference=reference,
            transfer_deadline=transfer_deadline,
        )
        streaming_download = getattr(self._downloader, "download_to_path", None)
        if callable(streaming_download):
            streaming_download(
                **transport,
                destination=destination,
            )
            verify_staged_file(reference=reference, path=destination)
            return

        content = self._downloader.download(**transport)
        try:
            verify_downloaded_content(reference=reference, content=content)
            with destination.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            del content
        verify_staged_file(reference=reference, path=destination)


__all__ = [
    "KnowledgeIndexWorkerArtifactStager",
    "file_sha256",
    "promote_staging",
    "strict_json_object",
    "verify_downloaded_content",
    "verify_staged_file",
]
