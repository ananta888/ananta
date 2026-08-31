"""Download ports for Hub admission of Worker-produced index artifacts."""

from __future__ import annotations

import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol


class KnowledgeIndexWorkerNoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep Worker capabilities on the single assigned-Worker request."""

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


class KnowledgeIndexArtifactTransferDeadlinePort(Protocol):
    def require_remaining_seconds(self) -> float: ...


class KnowledgeIndexWorkerArtifactDownloaderPort(Protocol):
    def download(
        self,
        *,
        worker_url: str,
        worker_token: str,
        reference: Mapping[str, Any],
        source_access_manifest: Mapping[str, Any] | None = None,
        job_id: str | None = None,
        transfer_deadline: KnowledgeIndexArtifactTransferDeadlinePort | None = None,
    ) -> bytes: ...


class KnowledgeIndexWorkerStreamingArtifactDownloaderPort(Protocol):
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
    ) -> None: ...
