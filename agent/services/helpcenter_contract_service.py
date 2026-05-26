from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_ROOT = Path(__file__).resolve().parents[2]
_HELP_CENTER_DIRNAME = "helpcenter"
_DEFAULT_README = """# Helpcenter

Analysis-only intake and reporting area for failure/support inputs.

## Security boundaries

- No automatic code fixes from Helpcenter inputs.
- Inputs and generated reports stay separated:
  - `inbox/` = normalized message inputs
  - `reports/` = generated analysis reports
  - `sources/` = source adapter metadata
  - `attachments/` = referenced attachments metadata
  - `index/` = fast lookup index
- Sensitive content must be redacted before worker/LLM exposure.
"""

HELP_CENTER_SUBDIRS: tuple[str, ...] = ("inbox", "reports", "sources", "attachments", "index")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _root(repo_root: str | Path | None = None) -> Path:
    base = Path(repo_root).resolve() if repo_root else _ROOT
    return base / _HELP_CENTER_DIRNAME


def ensure_helpcenter_structure(*, repo_root: str | Path | None = None) -> dict[str, Any]:
    root = _root(repo_root)
    root.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for name in HELP_CENTER_SUBDIRS:
        path = root / name
        if not path.exists():
            created.append(str(path.relative_to(root)))
        path.mkdir(parents=True, exist_ok=True)
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(_DEFAULT_README, encoding="utf-8")
    return {
        "root": str(root),
        "created_paths": created,
        "paths": {name: str((root / name).relative_to(root)) for name in HELP_CENTER_SUBDIRS},
        "readme_ref": "README.md",
    }


def _build_schema_validator(schema: dict[str, Any]) -> Draft202012Validator:
    return Draft202012Validator(schema)


def _collect_schema_issues(schema: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, str]]:
    validator = _build_schema_validator(schema)
    issues: list[dict[str, str]] = []
    for error in sorted(validator.iter_errors(payload), key=lambda err: list(err.path)):
        path = "/".join(map(str, error.path)) or "$"
        reason_code = "missing_required_field" if error.validator == "required" else "schema_validation_error"
        issues.append({"path": path, "reason_code": reason_code, "human_message": str(error.message)})
    return issues


def helpcenter_message_schema() -> dict[str, Any]:
    return {
        "$id": "https://ananta.dev/schemas/helpcenter-message-v1.json",
        "type": "object",
        "required": [
            "message_id",
            "source_kind",
            "source_ref",
            "received_at",
            "title",
            "severity",
            "normalized_summary",
            "labels",
            "privacy_class",
            "redaction_status",
        ],
        "additionalProperties": True,
        "properties": {
            "message_id": {"type": "string", "minLength": 1},
            "source_kind": {
                "type": "string",
                "enum": [
                    "github_workflow_failure",
                    "github_check_run",
                    "github_issue",
                    "imap_mail",
                    "cli_log",
                    "manual_note",
                    "custom",
                ],
            },
            "source_ref": {"type": "string", "minLength": 1},
            "received_at": {"type": "string", "minLength": 1},
            "title": {"type": "string", "minLength": 1},
            "severity": {"type": "string", "enum": ["info", "warning", "error", "critical"]},
            "raw_ref": {"type": "string"},
            "normalized_summary": {"type": "string", "minLength": 1},
            "labels": {"type": "array", "items": {"type": "string"}},
            "privacy_class": {"type": "string", "enum": ["internal", "restricted", "sensitive"]},
            "redaction_status": {"type": "string", "enum": ["not_required", "pending", "redacted", "blocked"]},
        },
    }


def helpcenter_analysis_schema() -> dict[str, Any]:
    return {
        "$id": "https://ananta.dev/schemas/helpcenter-analysis-v1.json",
        "type": "object",
        "required": [
            "analysis_id",
            "message_id",
            "generated_at",
            "status",
            "failure_summary",
            "likely_causes",
            "affected_files",
            "affected_tasks",
            "next_steps",
            "confidence",
            "source_refs",
            "provenance_refs",
            "machine_readable_findings",
            "human_summary",
            "no_auto_fix",
        ],
        "additionalProperties": True,
        "properties": {
            "analysis_id": {"type": "string", "minLength": 1},
            "message_id": {"type": "string", "minLength": 1},
            "generated_at": {"type": "string", "minLength": 1},
            "status": {"type": "string", "enum": ["draft", "ready", "degraded", "failed"]},
            "failure_summary": {"type": "string", "minLength": 1},
            "likely_causes": {"type": "array", "items": {"type": "string"}},
            "affected_files": {"type": "array", "items": {"type": "string"}},
            "affected_tasks": {"type": "array", "items": {"type": "string"}},
            "next_steps": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "source_refs": {"type": "array", "items": {"type": "string"}},
            "provenance_refs": {"type": "array", "items": {"type": "string"}},
            "machine_readable_findings": {"type": "array", "items": {"type": "object"}},
            "human_summary": {"type": "string", "minLength": 1},
            "no_auto_fix": {"type": "boolean", "const": True},
        },
    }


def helpcenter_index_schema() -> dict[str, Any]:
    return {
        "$id": "https://ananta.dev/schemas/helpcenter-index-v1.json",
        "type": "object",
        "required": ["schema", "generated_at", "reports", "latest_report_ref"],
        "additionalProperties": True,
        "properties": {
            "schema": {"type": "string", "const": "helpcenter_index.v1"},
            "generated_at": {"type": "string", "minLength": 1},
            "reports": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "analysis_id",
                        "message_id",
                        "status",
                        "severity",
                        "source_kind",
                        "created_at",
                        "report_ref",
                    ],
                    "properties": {
                        "analysis_id": {"type": "string", "minLength": 1},
                        "message_id": {"type": "string", "minLength": 1},
                        "status": {"type": "string", "minLength": 1},
                        "severity": {"type": "string", "minLength": 1},
                        "source_kind": {"type": "string", "minLength": 1},
                        "created_at": {"type": "string", "minLength": 1},
                        "report_ref": {"type": "string", "minLength": 1},
                    },
                },
            },
            "latest_report_ref": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    }


def validate_helpcenter_message(payload: dict[str, Any]) -> list[dict[str, str]]:
    return _collect_schema_issues(helpcenter_message_schema(), dict(payload or {}))


def validate_helpcenter_analysis(payload: dict[str, Any]) -> list[dict[str, str]]:
    return _collect_schema_issues(helpcenter_analysis_schema(), dict(payload or {}))


def validate_helpcenter_index(payload: dict[str, Any]) -> list[dict[str, str]]:
    return _collect_schema_issues(helpcenter_index_schema(), dict(payload or {}))


def default_helpcenter_index() -> dict[str, Any]:
    return {
        "schema": "helpcenter_index.v1",
        "generated_at": _now_iso(),
        "reports": [],
        "latest_report_ref": {},
    }


def load_helpcenter_index(*, repo_root: str | Path | None = None) -> dict[str, Any]:
    ensure_helpcenter_structure(repo_root=repo_root)
    index_path = _root(repo_root) / "index" / "helpcenter.index.json"
    if not index_path.exists():
        return default_helpcenter_index()
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("helpcenter_index_must_be_json_object")
    return payload


def upsert_helpcenter_index_entry(
    index_payload: dict[str, Any],
    *,
    analysis_id: str,
    message_id: str,
    status: str,
    severity: str,
    source_kind: str,
    created_at: str,
    report_ref: str,
) -> dict[str, Any]:
    next_payload = dict(index_payload or default_helpcenter_index())
    reports = [dict(item) for item in list(next_payload.get("reports") or []) if isinstance(item, dict)]
    reports = [item for item in reports if str(item.get("analysis_id") or "").strip() != str(analysis_id).strip()]
    reports.append(
        {
            "analysis_id": str(analysis_id).strip(),
            "message_id": str(message_id).strip(),
            "status": str(status).strip(),
            "severity": str(severity).strip(),
            "source_kind": str(source_kind).strip(),
            "created_at": str(created_at).strip(),
            "report_ref": str(report_ref).strip(),
        }
    )
    reports.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("analysis_id") or "")))
    latest = dict(next_payload.get("latest_report_ref") or {})
    latest[str(message_id).strip()] = str(report_ref).strip()
    next_payload["reports"] = reports
    next_payload["latest_report_ref"] = latest
    next_payload["generated_at"] = _now_iso()
    return next_payload


def write_helpcenter_index(index_payload: dict[str, Any], *, repo_root: str | Path | None = None) -> Path:
    issues = validate_helpcenter_index(index_payload)
    if issues:
        raise ValueError(f"helpcenter_index_invalid:{issues[0]['reason_code']}")
    ensure_helpcenter_structure(repo_root=repo_root)
    path = _root(repo_root) / "index" / "helpcenter.index.json"
    path.write_text(json.dumps(index_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_report_paths(
    *,
    analysis_id: str,
    report_date: date | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, str]:
    ensure_helpcenter_structure(repo_root=repo_root)
    target_date = report_date or datetime.now(UTC).date()
    day_dir = f"{target_date.year:04d}-{target_date.month:02d}-{target_date.day:02d}"
    base = _root(repo_root)
    markdown = base / "reports" / day_dir / f"{analysis_id}.md"
    raw_json = base / "reports" / day_dir / f"{analysis_id}.json"
    return {
        "markdown_ref": str(markdown.relative_to(base.parent)),
        "json_ref": str(raw_json.relative_to(base.parent)),
    }
