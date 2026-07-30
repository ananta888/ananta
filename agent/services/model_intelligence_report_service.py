"""Canonical JSON and offline HTML reports for model-intelligence analyses."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from agent.services.model_intelligence_artifact_store import (
    ModelIntelligenceArtifactRef,
    ModelIntelligenceArtifactStoreError,
    ModelIntelligenceArtifactStorePort,
)

REPORT_SCHEMA = "ananta.model-intelligence-report.v1"
SECTION_STATUSES = frozenset({"available", "unsupported", "not_run", "failed"})
_SECTION_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "credentials",
        "local_path",
        "password",
        "path",
        "private_key",
        "secret",
        "token",
    }
)


class ModelIntelligenceReportError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ModelIntelligenceReportSection:
    name: str
    status: str
    data: Any = field(default_factory=dict)
    reason_code: str | None = None
    artifact_refs: tuple[ModelIntelligenceArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        if not _SECTION_NAME.fullmatch(self.name):
            raise ModelIntelligenceReportError(
                "report_section_name_invalid",
                "report section name is invalid",
            )
        if self.status not in SECTION_STATUSES:
            raise ModelIntelligenceReportError(
                "report_section_status_invalid",
                "report section status is invalid",
            )
        if self.reason_code is not None and (
            not self.reason_code or len(self.reason_code) > 128
        ):
            raise ModelIntelligenceReportError(
                "report_section_reason_invalid",
                "report section reason code is invalid",
            )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "artifact_refs": [
                reference.to_dict()
                for reference in sorted(
                    self.artifact_refs,
                    key=lambda item: (item.digest, item.artifact_kind, item.media_type),
                )
            ],
            "data": _redact_json(self.data),
            "name": self.name,
            "status": self.status,
        }
        if self.reason_code is not None:
            result["reason_code"] = self.reason_code
        return result


@dataclass(frozen=True)
class RenderedModelIntelligenceReport:
    canonical_json: bytes
    content_digest: str
    offline_html: bytes
    volatile_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoredModelIntelligenceReport:
    content_digest: str
    json_ref: ModelIntelligenceArtifactRef
    html_ref: ModelIntelligenceArtifactRef


class ModelIntelligenceReportService:
    """Create and persist deterministic reports without orchestration concerns."""

    def __init__(
        self,
        *,
        artifact_store: ModelIntelligenceArtifactStorePort | None = None,
    ) -> None:
        self._artifact_store = artifact_store

    def render(
        self,
        *,
        model_identity: Mapping[str, Any],
        tool_versions: Mapping[str, str],
        sections: Sequence[ModelIntelligenceReportSection],
        volatile_metadata: Mapping[str, Any] | None = None,
    ) -> RenderedModelIntelligenceReport:
        section_names = [section.name for section in sections]
        if len(section_names) != len(set(section_names)):
            raise ModelIntelligenceReportError(
                "report_section_duplicate",
                "report section names must be unique",
            )
        payload = {
            "model_identity": _redact_json(model_identity),
            "schema": REPORT_SCHEMA,
            "sections": [
                section.to_dict()
                for section in sorted(sections, key=lambda item: item.name)
            ],
            "tool_versions": _canonical_tool_versions(tool_versions),
        }
        canonical_json = _canonical_json_bytes(payload) + b"\n"
        content_digest = f"sha256:{hashlib.sha256(canonical_json).hexdigest()}"
        return RenderedModelIntelligenceReport(
            canonical_json=canonical_json,
            content_digest=content_digest,
            offline_html=_render_offline_html(payload, content_digest),
            volatile_metadata=_redact_json(dict(volatile_metadata or {})),
        )

    def persist(
        self,
        tenant_id: str,
        report: RenderedModelIntelligenceReport,
    ) -> StoredModelIntelligenceReport:
        store = self._require_store()
        json_ref = store.put_bytes(
            tenant_id,
            report.canonical_json,
            media_type="application/json",
            artifact_kind="model-intelligence-report-json",
            expected_digest=report.content_digest,
        )
        html_ref = store.put_bytes(
            tenant_id,
            report.offline_html,
            media_type="text/html; charset=utf-8",
            artifact_kind="model-intelligence-report-html",
        )
        return StoredModelIntelligenceReport(
            content_digest=report.content_digest,
            json_ref=json_ref,
            html_ref=html_ref,
        )

    def load(
        self,
        tenant_id: str,
        reference: ModelIntelligenceArtifactRef,
        *,
        validate_references: bool = True,
    ) -> dict[str, Any]:
        if reference.media_type != "application/json":
            raise ModelIntelligenceReportError(
                "report_media_type_invalid",
                "report artifact must be canonical JSON",
            )
        store = self._require_store()
        try:
            content = store.get_bytes(tenant_id, reference)
        except ModelIntelligenceArtifactStoreError:
            raise
        try:
            payload = json.loads(content)
        except (UnicodeError, ValueError) as exc:
            raise ModelIntelligenceReportError(
                "report_json_invalid",
                "report artifact is not valid JSON",
            ) from exc
        _validate_report_payload(payload)
        if content != _canonical_json_bytes(payload) + b"\n":
            raise ModelIntelligenceReportError(
                "report_not_canonical",
                "report JSON is not canonically encoded",
            )
        if validate_references:
            for section in payload["sections"]:
                for raw_reference in section["artifact_refs"]:
                    try:
                        child_reference = ModelIntelligenceArtifactRef.from_dict(raw_reference)
                        store.get_metadata(tenant_id, child_reference)
                    except ModelIntelligenceArtifactStoreError as exc:
                        raise ModelIntelligenceReportError(
                            "report_artifact_reference_invalid",
                            "report references an unavailable artifact",
                        ) from exc
        return payload

    def _require_store(self) -> ModelIntelligenceArtifactStorePort:
        if self._artifact_store is None:
            raise ModelIntelligenceReportError(
                "report_artifact_store_required",
                "report persistence requires an artifact store",
            )
        return self._artifact_store


def _canonical_tool_versions(value: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_version in value.items():
        name = str(raw_name).strip()
        version = str(raw_version).strip()
        if (
            not name
            or len(name) > 128
            or not version
            or len(version) > 256
        ):
            raise ModelIntelligenceReportError(
                "report_tool_version_invalid",
                "tool version entry is invalid",
            )
        result[name] = version
    return {name: result[name] for name in sorted(result)}


def _redact_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 20:
        raise ModelIntelligenceReportError(
            "report_payload_too_deep",
            "report payload exceeds its nesting bound",
        )
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ModelIntelligenceReportError(
                "report_number_invalid",
                "report payload contains a non-finite number",
            )
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, child in value.items():
            if not isinstance(raw_key, str) or not raw_key or len(raw_key) > 256:
                raise ModelIntelligenceReportError(
                    "report_key_invalid",
                    "report payload contains an invalid key",
                )
            normalized_key = re.sub(r"[^a-z0-9]+", "_", raw_key.lower()).strip("_")
            result[raw_key] = (
                _REDACTED
                if normalized_key in _SENSITIVE_KEYS
                else _redact_json(child, depth=depth + 1)
            )
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        if len(value) > 100_000:
            raise ModelIntelligenceReportError(
                "report_collection_too_large",
                "report payload collection exceeds its bound",
            )
        return [_redact_json(item, depth=depth + 1) for item in value]
    raise ModelIntelligenceReportError(
        "report_value_invalid",
        "report payload contains a non-JSON value",
    )


def _validate_report_payload(payload: Any) -> None:
    if (
        not isinstance(payload, dict)
        or set(payload) != {
            "model_identity",
            "schema",
            "sections",
            "tool_versions",
        }
        or payload.get("schema") != REPORT_SCHEMA
        or not isinstance(payload.get("model_identity"), dict)
        or not isinstance(payload.get("tool_versions"), dict)
        or not isinstance(payload.get("sections"), list)
    ):
        raise ModelIntelligenceReportError(
            "report_schema_invalid",
            "report payload does not match the report schema",
        )
    names: list[str] = []
    for section in payload["sections"]:
        if not isinstance(section, dict):
            raise ModelIntelligenceReportError(
                "report_schema_invalid",
                "report section is invalid",
            )
        allowed = {"artifact_refs", "data", "name", "reason_code", "status"}
        required = {"artifact_refs", "data", "name", "status"}
        if (
            not required.issubset(section)
            or not set(section).issubset(allowed)
            or not isinstance(section["artifact_refs"], list)
            or not isinstance(section["name"], str)
            or not _SECTION_NAME.fullmatch(section["name"])
            or section["status"] not in SECTION_STATUSES
            or (
                "reason_code" in section
                and (
                    not isinstance(section["reason_code"], str)
                    or not section["reason_code"]
                )
            )
        ):
            raise ModelIntelligenceReportError(
                "report_schema_invalid",
                "report section does not match the report schema",
            )
        names.append(section["name"])
    if names != sorted(names) or len(names) != len(set(names)):
        raise ModelIntelligenceReportError(
            "report_schema_invalid",
            "report sections are not uniquely sorted",
        )
    _redact_json(payload)


def _canonical_json_bytes(payload: Any) -> bytes:
    try:
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ModelIntelligenceReportError(
            "report_json_invalid",
            "report payload is not canonical JSON",
        ) from exc


def _render_offline_html(payload: Mapping[str, Any], content_digest: str) -> bytes:
    canonical = _canonical_json_bytes(payload).decode("ascii")
    escaped_json = html.escape(canonical, quote=True)
    escaped_digest = html.escape(content_digest, quote=True)
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ananta Model Intelligence Report</title>
<style>
body{{background:#f4f0e6;color:#15241f;font-family:Georgia,serif;margin:0;padding:2rem}}
main{{background:#fffdf7;border:1px solid #b9ad92;box-shadow:6px 6px 0 #15241f;margin:auto;max-width:76rem;padding:2rem}}
h1{{font-size:2rem;margin:0 0 .5rem}}code,pre{{font-family:"Courier New",monospace}}
.digest{{overflow-wrap:anywhere}}pre{{background:#15241f;color:#f7f1df;overflow:auto;padding:1rem;white-space:pre-wrap}}
</style>
</head>
<body><main>
<h1>Model Intelligence Report</h1>
<p class="digest"><strong>Content digest:</strong> <code>{escaped_digest}</code></p>
<pre>{escaped_json}</pre>
</main></body>
</html>
"""
    return document.encode("utf-8")


__all__ = [
    "ModelIntelligenceReportError",
    "ModelIntelligenceReportSection",
    "ModelIntelligenceReportService",
    "REPORT_SCHEMA",
    "RenderedModelIntelligenceReport",
    "SECTION_STATUSES",
    "StoredModelIntelligenceReport",
]
