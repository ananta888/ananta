from __future__ import annotations

from typing import Any


class BlenderHubClient:
    def __init__(self, endpoint: str, token: str | None = None) -> None:
        self.endpoint = str(endpoint or '').rstrip('/')
        self.token = token or ''

    def health(self) -> dict[str, Any]:
        return {"status": "ok" if self.endpoint else "degraded", "endpoint": self.endpoint}
