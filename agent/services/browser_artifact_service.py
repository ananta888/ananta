from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent.services.artifact_store import get_artifact_store


@dataclass(frozen=True)
class BrowserArtifactValidation:
    valid: bool
    reason: str


class BrowserArtifactService:
    REQUIRED = {"extracted_data", "page_evidence", "sources"}

    def validate_schema(self, payload: dict[str, Any]) -> BrowserArtifactValidation:
        missing = [k for k in self.REQUIRED if k not in payload]
        if missing:
            return BrowserArtifactValidation(False, f"browser_artifact_missing_fields:{','.join(sorted(missing))}")
        if not isinstance(payload.get("sources"), list):
            return BrowserArtifactValidation(False, "browser_artifact_sources_not_list")
        return BrowserArtifactValidation(True, "ok")

    def persist_with_provenance(self, *, artifact_id: str, version_number: int, payload: dict[str, Any]) -> dict[str, Any]:
        check = self.validate_schema(payload)
        if not check.valid:
            raise ValueError(check.reason)

        content = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        storage = get_artifact_store().store_bytes(
            artifact_id=artifact_id,
            version_number=version_number,
            filename="browser-artifact.json",
            content=content,
            media_type="application/json",
        )
        return {
            "artifact_id": artifact_id,
            "version_number": version_number,
            "storage": storage,
            "provenance": {
                "schema": "browser-artifact.v1",
                "source": "browser_use",
            },
        }

    def verify_completion_gate(
        self,
        *,
        payload: dict[str, Any],
        min_source_count: int = 1,
        require_evidence: bool = True,
    ) -> BrowserArtifactValidation:
        check = self.validate_schema(payload)
        if not check.valid:
            return check

        sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
        extracted = payload.get("extracted_data") if isinstance(payload.get("extracted_data"), dict) else {}
        evidence = payload.get("page_evidence") if isinstance(payload.get("page_evidence"), list) else []

        if int(min_source_count) > 0 and len(sources) < int(min_source_count):
            return BrowserArtifactValidation(False, "browser_artifact_min_sources_not_met")
        if require_evidence and (not evidence or not extracted):
            return BrowserArtifactValidation(False, "browser_artifact_required_evidence_missing")
        return BrowserArtifactValidation(True, "ok")


_SERVICE = BrowserArtifactService()


def get_browser_artifact_service() -> BrowserArtifactService:
    return _SERVICE
