"""Strict validation for untrusted collaboration content references."""

from __future__ import annotations

import hashlib
import ipaddress
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from agent.services.collaboration_content_security import CollaborationSensitiveContentDetector
from ananta_contracts.collaboration_workspace import require_digest, require_id


class CollaborationContentIngressPolicy:
    """Validates typed data before prompt construction or durable admission."""

    LIMITS = {
        "url": 2_048,
        "markdown": 262_144,
        "html": 262_144,
        "patch": 1_048_576,
        "attachment": 100_000_000,
        "tool_output": 262_144,
    }
    MEDIA_TYPES = {
        "url": frozenset({"text/uri-list"}),
        "markdown": frozenset({"text/markdown", "text/plain"}),
        "html": frozenset({"text/html"}),
        "patch": frozenset({"text/x-diff", "text/plain"}),
        "attachment": frozenset({"application/json", "application/pdf", "image/jpeg", "image/png", "text/plain"}),
        "tool_output": frozenset({"application/json", "text/plain"}),
    }
    FIELDS = {
        "kind",
        "content_id",
        "media_type",
        "encoding",
        "size_bytes",
        "content_digest",
        "content",
        "source_url",
        "relative_path",
        "scan_status",
    }

    def __init__(self, detector: CollaborationSensitiveContentDetector | None = None) -> None:
        self._detector = detector or CollaborationSensitiveContentDetector()

    def validate(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if set(value) != self.FIELDS:
            raise ValueError("collaboration_content_ingress_fields_invalid")
        kind = str(value.get("kind") or "")
        media_type = str(value.get("media_type") or "").strip().casefold()
        encoding = str(value.get("encoding") or "").strip().casefold()
        size = value.get("size_bytes")
        content = value.get("content")
        if (
            kind not in self.LIMITS
            or media_type not in self.MEDIA_TYPES[kind]
            or encoding != "utf-8"
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 <= size <= self.LIMITS[kind]
            or value.get("scan_status") != "clean"
        ):
            raise ValueError("collaboration_content_ingress_invalid")
        digest = require_digest(value.get("content_digest"), "content_digest")
        normalized_content = self._content(kind, content, size, digest)
        source_url = self._url(value.get("source_url"), required=kind == "url")
        relative_path = self._path(value.get("relative_path"))
        if kind == "url" and normalized_content != source_url:
            raise ValueError("collaboration_content_ingress_url_mismatch")
        violation = self._detector.sensitive_path(normalized_content, "content")
        if violation is not None:
            raise ValueError(f"collaboration_sensitive_content_rejected:{violation}")
        return {
            "kind": kind,
            "content_id": require_id(value.get("content_id"), "content_id"),
            "media_type": media_type,
            "encoding": "utf-8",
            "size_bytes": size,
            "content_digest": digest,
            "content": normalized_content,
            "source_url": source_url,
            "relative_path": relative_path,
            "scan_status": "clean",
        }

    @staticmethod
    def _content(kind: str, value: Any, size: int, digest: str) -> str | None:
        if kind == "attachment":
            if value is not None:
                raise ValueError("collaboration_attachment_inline_content_rejected")
            return None
        if not isinstance(value, str):
            raise ValueError("collaboration_content_ingress_content_invalid")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError("collaboration_content_ingress_encoding_invalid") from exc
        if len(encoded) != size or hashlib.sha256(encoded).hexdigest() != digest:
            raise ValueError("collaboration_content_ingress_digest_or_size_mismatch")
        return value

    @staticmethod
    def _url(value: Any, *, required: bool) -> str | None:
        if value is None and not required:
            return None
        if not isinstance(value, str) or len(value) > 2_048:
            raise ValueError("collaboration_content_ingress_url_invalid")
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("collaboration_content_ingress_url_invalid")
        host = parsed.hostname.casefold().rstrip(".")
        if host == "localhost" or host.endswith(".localhost"):
            raise ValueError("collaboration_content_ingress_url_private")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("collaboration_content_ingress_url_private")
        return value

    @staticmethod
    def _path(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value or len(value) > 512 or "\\" in value or "\x00" in value:
            raise ValueError("collaboration_content_ingress_path_invalid")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
            raise ValueError("collaboration_content_ingress_path_invalid")
        return path.as_posix()


__all__ = ["CollaborationContentIngressPolicy"]
