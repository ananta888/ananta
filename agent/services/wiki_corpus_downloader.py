"""Resumable, size- and storage-bounded HTTP download of wiki corpora and indexes."""

from __future__ import annotations

import logging
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ResumableCorpusDownloader:
    """Download one URL into ``destination``, resuming a partial file with a Range request.

    ``opener`` defaults to :func:`urllib.request.urlopen`, resolved per call so the
    standard library seam keeps working; tests may inject a fake opener instead.
    """

    def __init__(self, *, opener: Callable[..., Any] | None = None) -> None:
        self._opener = opener

    def download(
        self,
        *,
        url: str,
        destination: Path,
        max_download_bytes: int,
        cancel_check=None,
    ) -> dict[str, Any]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        existing_bytes = destination.stat().st_size if destination.exists() else 0
        request = urllib.request.Request(url)
        mode = "wb"
        requested_range = False
        if existing_bytes > 0:
            request.add_header("Range", f"bytes={existing_bytes}-")
            mode = "ab"
            requested_range = True
        downloaded_bytes = existing_bytes
        status = 0
        response_headers: dict[str, Any] = {}
        restarted_without_range = False
        storage_issue = None
        opener = self._opener or urllib.request.urlopen
        try:
            _response_ctx = opener(request, timeout=45)
        except urllib.error.HTTPError as http_err:
            if http_err.code == 416 and existing_bytes > 0:
                # 416 Range Not Satisfiable: the local file already covers the full content —
                # the previous download completed but the process was killed before the job finished.
                logger.info(
                    "_download_with_resume: 416 for %s — local file (%d bytes) is complete, reusing",
                    url,
                    existing_bytes,
                )
                return {
                    "bytes": int(existing_bytes),
                    "status_code": 416,
                    "requested_range": True,
                    "resumed": False,
                    "already_complete": True,
                    "headers": {},
                    "storage_issue": None,
                }
            raise
        with _response_ctx as response:
            status = int(getattr(response, "status", 200) or 200)
            headers = getattr(response, "headers", {}) or {}
            response_headers = {
                "content_length": str(headers.get("Content-Length") or "").strip() or None,
                "accept_ranges": str(headers.get("Accept-Ranges") or "").strip() or None,
                "etag": str(headers.get("ETag") or "").strip() or None,
                "last_modified": str(headers.get("Last-Modified") or "").strip() or None,
            }
            if existing_bytes > 0 and status == 200:
                mode = "wb"
                downloaded_bytes = 0
                restarted_without_range = True
            content_length = 0
            try:
                content_length = int(headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                content_length = 0
            expected_remaining = max(0, content_length)
            free_bytes = shutil.disk_usage(destination.parent).free
            reserve_bytes = max(128 * 1024 * 1024, max_download_bytes // 200)
            if expected_remaining > 0 and free_bytes < expected_remaining + reserve_bytes:
                storage_issue = {
                    "free_bytes": int(free_bytes),
                    "required_bytes": int(expected_remaining + reserve_bytes),
                    "reserve_bytes": int(reserve_bytes),
                }
                raise ValueError("wiki_storage_insufficient")
            with destination.open(mode) as output:
                while True:
                    if cancel_check and cancel_check():
                        raise ValueError("wiki_download_cancelled")
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > max_download_bytes:
                        raise ValueError("wiki_corpus_too_large")
                    output.write(chunk)
        return {
            "bytes": int(downloaded_bytes),
            "status_code": int(status),
            "requested_range": requested_range,
            "resumed": bool(requested_range and not restarted_without_range),
            "restarted_without_range": restarted_without_range,
            "headers": response_headers,
            "storage_issue": storage_issue,
        }


__all__ = ["ResumableCorpusDownloader"]
