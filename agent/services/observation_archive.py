"""Observation Archive: keep full tool output, expose compact, scope-safe handles (SEH-006).

Large tool observations are archived **unchanged** and addressed by content hash.
The active context only carries a small handle (id, digest, size, preview). Recall
returns exactly the archived bytes and is refused for a handle that was issued in
another tenant/project/task/run scope, so handles are not transferable.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from ananta_contracts.harness_genome import content_digest

DEFAULT_PREVIEW_CHARS = 280
# Rough, provider-neutral estimate; the runtime may replace it with a real tokenizer.
_CHARS_PER_TOKEN = 4


def _token_estimate(text: str) -> int:
    return math.ceil(len(text) / _CHARS_PER_TOKEN)


@dataclass(frozen=True, slots=True)
class ObservationScope:
    tenant_id: str
    project_id: str
    task_id: str
    run_id: str

    def assert_valid(self) -> None:
        for name, value in (("tenant_id", self.tenant_id), ("project_id", self.project_id),
                            ("task_id", self.task_id), ("run_id", self.run_id)):
            if not isinstance(value, str) or not value or len(value) > 200:
                raise ValueError(f"observation_scope_{name}_invalid")

    def key(self) -> str:
        self.assert_valid()
        return content_digest([self.tenant_id, self.project_id, self.task_id, self.run_id])


@dataclass(frozen=True, slots=True)
class ObservationHandle:
    handle_id: str
    scope_key: str
    digest: str
    byte_size: int
    token_estimate: int
    media_type: str
    preview: str

    def to_mapping(self) -> dict:
        return {"handle_id": self.handle_id, "digest": self.digest, "byte_size": self.byte_size,
                "token_estimate": self.token_estimate, "media_type": self.media_type, "preview": self.preview}


@dataclass
class ObservationArchive:
    """Content-addressed archive with a pluggable backend port."""

    store: dict[str, str] = field(default_factory=dict)
    _handles: dict[str, tuple[str, str]] = field(default_factory=dict)  # handle_id -> (scope_key, digest)
    preview_chars: int = DEFAULT_PREVIEW_CHARS
    clock: Callable[[], float] | None = None

    def store_observation(self, scope: ObservationScope, content: str, *, media_type: str = "text/plain",
                          preview_chars: int | None = None) -> ObservationHandle:
        scope.assert_valid()
        if not isinstance(content, str):
            raise ValueError("observation_content_invalid")
        if not isinstance(media_type, str) or not media_type or len(media_type) > 100:
            raise ValueError("observation_media_type_invalid")
        digest = content_digest(content)
        self.store[digest] = content
        scope_key = scope.key()
        handle_id = content_digest({"scope": scope_key, "digest": digest})
        self._handles[handle_id] = (scope_key, digest)
        width = preview_chars if preview_chars is not None else self.preview_chars
        if type(width) is not int or not 0 <= width <= 4000:
            raise ValueError("observation_preview_width_invalid")
        return ObservationHandle(handle_id=handle_id, scope_key=scope_key, digest=digest,
                                 byte_size=len(content.encode("utf-8")), token_estimate=_token_estimate(content),
                                 media_type=media_type, preview=content[:width])

    def recall(self, scope: ObservationScope, handle_id: str) -> str:
        scope.assert_valid()
        binding = self._handles.get(handle_id)
        if binding is None:
            raise ValueError("observation_handle_unknown")
        scope_key, digest = binding
        if scope_key != scope.key():
            raise ValueError("observation_handle_scope_mismatch")
        content = self.store.get(digest)
        if content is None or content_digest(content) != digest:
            raise ValueError("observation_content_missing_or_corrupt")
        return content

    def recall_range(self, scope: ObservationScope, handle_id: str, start: int, end: int) -> str:
        content = self.recall(scope, handle_id)
        if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(content):
            raise ValueError("observation_range_invalid")
        return content[start:end]

    def token_savings(self, handle: ObservationHandle) -> int:
        """Tokens saved in the active context by carrying the handle instead of the original."""
        return max(0, handle.token_estimate - _token_estimate(handle.preview))
