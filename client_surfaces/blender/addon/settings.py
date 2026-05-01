from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class BlenderAddonSettings:
    endpoint: str = ""
    token: str | None = None
    token_ref: str = ""
    profile: str = "blender"
    transport_mode: str = "stub"
    request_timeout_seconds: int = 30
    max_context_objects: int = 128
    max_payload_bytes: int = 32768
    allow_insecure_http: bool = False

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "BlenderAddonSettings":
        data = dict(payload or {})
        return cls(
            endpoint=str(data.get("endpoint") or "").rstrip("/"),
            token=str(data.get("token") or "") or None,
            token_ref=str(data.get("token_ref") or ""),
            profile=str(data.get("profile") or "blender").strip() or "blender",
            transport_mode=str(data.get("transport_mode") or "stub").strip().lower() or "stub",
            request_timeout_seconds=max(1, int(data.get("request_timeout_seconds") or 30)),
            max_context_objects=max(1, min(int(data.get("max_context_objects") or 128), 512)),
            max_payload_bytes=max(1024, min(int(data.get("max_payload_bytes") or 32768), 262144)),
            allow_insecure_http=bool(data.get("allow_insecure_http")),
        )

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> "BlenderAddonSettings":
        env = environ or os.environ
        return cls.from_mapping(
            {
                "endpoint": env.get("ANANTA_BLENDER_ENDPOINT") or env.get("ANANTA_HUB_ENDPOINT") or "",
                "token": env.get("ANANTA_BLENDER_TOKEN") or env.get("ANANTA_HUB_TOKEN") or "",
                "token_ref": env.get("ANANTA_BLENDER_TOKEN_REF") or "",
                "profile": env.get("ANANTA_BLENDER_PROFILE") or "blender",
                "transport_mode": env.get("ANANTA_BLENDER_TRANSPORT") or "stub",
                "request_timeout_seconds": env.get("ANANTA_BLENDER_TIMEOUT_SECONDS") or 30,
                "max_context_objects": env.get("ANANTA_BLENDER_MAX_CONTEXT_OBJECTS") or 128,
                "max_payload_bytes": env.get("ANANTA_BLENDER_MAX_PAYLOAD_BYTES") or 32768,
                "allow_insecure_http": str(env.get("ANANTA_BLENDER_ALLOW_INSECURE_HTTP") or "").lower()
                in {"1", "true", "yes"},
            }
        )

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.endpoint:
            problems.append("endpoint_missing")
        if self.endpoint.startswith("http://") and not self.allow_insecure_http:
            problems.append("insecure_http_requires_opt_in")
        if self.transport_mode not in {"stub", "http"}:
            problems.append("invalid_transport_mode")
        if not self.profile:
            problems.append("profile_missing")
        return problems

    def to_redacted_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload.get("token"):
            payload["token"] = "***redacted***"
        if payload.get("token_ref"):
            payload["token_ref"] = "***redacted***"
        return payload
