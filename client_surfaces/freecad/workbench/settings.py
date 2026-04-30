from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FreecadWorkbenchSettings:
    endpoint: str
    profile: str = "default"
    token: str | None = None
    request_timeout_seconds: int = 30
    max_context_objects: int = 128
    max_payload_bytes: int = 32768
    allow_insecure_http: bool = False

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "FreecadWorkbenchSettings":
        data = dict(payload or {})
        return cls(
            endpoint=str(data.get("endpoint") or "").rstrip("/"),
            profile=str(data.get("profile") or "default"),
            token=str(data.get("token") or "") or None,
            request_timeout_seconds=max(1, int(data.get("request_timeout_seconds") or 30)),
            max_context_objects=max(1, min(int(data.get("max_context_objects") or 128), 256)),
            max_payload_bytes=max(1024, int(data.get("max_payload_bytes") or 32768)),
            allow_insecure_http=bool(data.get("allow_insecure_http")),
        )

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.endpoint:
            problems.append("endpoint_missing")
        if self.endpoint.startswith("http://") and not self.allow_insecure_http:
            problems.append("insecure_http_requires_opt_in")
        return problems

    def to_redacted_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload.get("token"):
            payload["token"] = "***redacted***"
        return payload
