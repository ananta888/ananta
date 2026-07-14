"""Worker-side identity headers for scoped Hub service requests."""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowServiceIdentity:
    worker_id: str
    worker_url: str

    @classmethod
    def optional(
        cls,
        *,
        worker_id: str = "",
        worker_url: str = "",
    ) -> "WorkflowServiceIdentity | None":
        normalized_id = _optional_header_identity(worker_id)
        normalized_url = _optional_worker_url(worker_url)
        if bool(normalized_id) != bool(normalized_url):
            raise ValueError("workflow Worker ID and URL are both required")
        if not normalized_id:
            return None
        return cls(worker_id=normalized_id, worker_url=normalized_url)

    @classmethod
    def from_environment(
        cls,
        source: Mapping[str, str],
    ) -> "WorkflowServiceIdentity | None":
        return cls.optional(
            worker_id=str(source.get("AGENT_NAME") or "").strip(),
            worker_url=str(source.get("AGENT_URL") or "").strip(),
        )

    def headers(self) -> dict[str, str]:
        return {
            "X-Ananta-Worker-ID": self.worker_id,
            "X-Ananta-Worker-URL": self.worker_url,
        }


def _optional_header_identity(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if (
        len(value.encode("utf-8")) > 256
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        raise ValueError("workflow Worker ID is invalid")
    return value


def _optional_worker_url(raw: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
        parsed.port
    except ValueError as exc:
        raise ValueError("workflow Worker URL is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "\r" in value
        or "\n" in value
    ):
        raise ValueError("workflow Worker URL is invalid")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


__all__ = ["WorkflowServiceIdentity"]
