"""Policy boundary for attaching image bytes to local-runtime requests."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from agent.services.local_runtime_capability_contracts import RuntimeModelSnapshot


@dataclass(frozen=True, slots=True)
class LocalRuntimeImage:
    mime_type: str
    data: bytes


class LocalRuntimeImageAdapter:
    def __init__(self, *, maximum_bytes: int = 10 * 1024 * 1024) -> None:
        self._maximum_bytes = max(1, min(int(maximum_bytes), 100 * 1024 * 1024))

    def encode(self, image: LocalRuntimeImage, *, snapshot: RuntimeModelSnapshot) -> str:
        if not snapshot.routable("vision"):
            raise ValueError("local_runtime_vision_capability_required")
        if image.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError("local_runtime_image_mime_denied")
        if not image.data or len(image.data) > self._maximum_bytes:
            raise ValueError("local_runtime_image_size_denied")
        return base64.b64encode(image.data).decode("ascii")

    def attach_to_ollama_messages(
        self,
        messages: list[dict[str, Any]],
        images: list[LocalRuntimeImage],
        *,
        snapshot: RuntimeModelSnapshot,
    ) -> list[dict[str, Any]]:
        if not images:
            return [dict(item) for item in messages]
        if len(images) > 8:
            raise ValueError("local_runtime_image_count_denied")
        result = [dict(item) for item in messages]
        target = next(
            (
                item
                for item in reversed(result)
                if str(item.get("role") or "").strip().lower() == "user"
            ),
            None,
        )
        if target is None:
            raise ValueError("local_runtime_image_user_message_required")
        target["images"] = [self.encode(item, snapshot=snapshot) for item in images]
        return result


__all__ = ["LocalRuntimeImage", "LocalRuntimeImageAdapter"]
