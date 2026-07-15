from __future__ import annotations

from collections.abc import Mapping

from .base import VoiceBackend


class ReadOnlyVoiceBackendCatalog:
    """Project lightweight readiness metadata for a fixed backend allowlist.

    Backend adapters own their readiness probes.  This projection only merges
    their bounded metadata and converts a failed probe into an unavailable
    entry; it never calls transcription or model-loading methods.
    """

    def __init__(self, backends: Mapping[str, VoiceBackend]) -> None:
        if not backends:
            raise ValueError("voice backend catalog requires at least one backend")
        self._backends = tuple((str(backend_id), backend) for backend_id, backend in backends.items())

    def list_models(self) -> list[dict[str, object]]:
        models: list[dict[str, object]] = []
        for backend_id, backend in self._backends:
            try:
                reported = backend.list_models()
                entries = [item for item in reported if isinstance(item, dict)]
            except Exception:
                entries = []
            if not entries:
                models.append(self._unavailable(backend_id, reason_code=f"{backend_id}.catalog_probe_failed"))
                continue
            for item in entries:
                projected: dict[str, object] = {
                    **item,
                    "engine": str(item.get("engine") or backend_id),
                }
                status = str(projected.get("status") or "unavailable").strip().lower()
                if status == "unavailable" and not str(projected.get("reason_code") or "").strip():
                    projected["reason_code"] = f"{backend_id}.runtime_unavailable"
                models.append(projected)
        return models

    @staticmethod
    def _unavailable(backend_id: str, *, reason_code: str) -> dict[str, object]:
        return {
            "id": backend_id,
            "display_name": backend_id,
            "engine": backend_id,
            "capabilities": [],
            "status": "unavailable",
            "reason_code": reason_code,
        }
