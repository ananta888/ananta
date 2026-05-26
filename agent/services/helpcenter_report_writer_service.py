from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.services.helpcenter_contract_service import (
    build_report_paths,
    ensure_helpcenter_structure,
    load_helpcenter_index,
    upsert_helpcenter_index_entry,
    validate_helpcenter_analysis,
    validate_helpcenter_message,
    write_helpcenter_index,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _project_root(repo_root: str | Path | None = None) -> Path:
    return Path(repo_root).resolve() if repo_root else Path(__file__).resolve().parents[2]


def _markdown_report(*, message: dict[str, Any], analysis: dict[str, Any], json_ref: str) -> str:
    likely_causes = [str(item) for item in list(analysis.get("likely_causes") or []) if str(item).strip()]
    affected_files = [str(item) for item in list(analysis.get("affected_files") or []) if str(item).strip()]
    next_steps = [str(item) for item in list(analysis.get("next_steps") or []) if str(item).strip()]
    provenance = [str(item) for item in list(analysis.get("provenance_refs") or []) if str(item).strip()]
    source_kind = str(message.get("source_kind") or "").strip()
    source_ref = str(message.get("source_ref") or "").strip()
    lines = [
        f"# Helpcenter Analysis {analysis.get('analysis_id')}",
        "",
        "> analysis only, no auto fix",
        "",
        "## Source",
        f"- kind: {source_kind}",
        f"- ref: {source_ref}",
        "",
        "## Failure Summary",
        str(analysis.get("failure_summary") or ""),
        "",
        "## Likely Causes",
        *(f"- {item}" for item in likely_causes or ["- n/a"]),
        "",
        "## Affected Files",
        *(f"- {item}" for item in affected_files or ["- n/a"]),
        "",
        "## Next Steps",
        *(f"- {item}" for item in next_steps or ["- n/a"]),
        "",
        "## Provenance",
        *(f"- {item}" for item in provenance or ["- n/a"]),
        "",
        f"JSON ref: {json_ref}",
    ]
    return "\n".join(lines).strip() + "\n"


def write_helpcenter_report(
    *,
    message: dict[str, Any],
    analysis: dict[str, Any],
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    message_payload = dict(message or {})
    analysis_payload = dict(analysis or {})
    message_issues = validate_helpcenter_message(message_payload)
    if message_issues:
        raise ValueError(f"helpcenter_message_invalid:{message_issues[0]['reason_code']}")
    analysis_issues = validate_helpcenter_analysis(analysis_payload)
    if analysis_issues:
        raise ValueError(f"helpcenter_analysis_invalid:{analysis_issues[0]['reason_code']}")

    ensure_helpcenter_structure(repo_root=repo_root)
    analysis_id = str(analysis_payload.get("analysis_id") or "").strip()
    if not analysis_id:
        raise ValueError("helpcenter_analysis_missing_analysis_id")
    analysis_payload.setdefault("no_auto_fix", True)
    analysis_payload.setdefault("status", "ready")
    analysis_payload["source_refs"] = [
        str(item) for item in list(analysis_payload.get("source_refs") or []) if str(item).strip()
    ]
    analysis_payload["provenance_refs"] = [
        str(item) for item in list(analysis_payload.get("provenance_refs") or []) if str(item).strip()
    ]
    analysis_payload.setdefault("generated_at", _now_iso())
    analysis_payload["redaction_status"] = str(message_payload.get("redaction_status") or "pending")
    analysis_payload["source_kind"] = str(message_payload.get("source_kind") or "").strip()
    analysis_payload["severity"] = str(message_payload.get("severity") or "warning").strip()
    if isinstance(message_payload.get("meta"), dict):
        analysis_payload["source_metadata"] = dict(message_payload.get("meta") or {})

    index = load_helpcenter_index(repo_root=repo_root)
    message_id = str(message_payload.get("message_id") or "").strip()
    siblings = [
        dict(item)
        for item in list(index.get("reports") or [])
        if str(item.get("message_id") or "").strip() == message_id
    ]
    version = len(siblings) + 1
    duplicate_of = str(siblings[-1].get("analysis_id") or "").strip() if siblings else ""
    stem = _report_stem(message_payload, analysis_id=analysis_id, version=version)
    refs = build_report_paths(analysis_id=analysis_id, report_stem=stem, repo_root=repo_root)
    project_root = _project_root(repo_root)
    markdown_path = project_root / str(refs["markdown_ref"])
    json_path = project_root / str(refs["json_ref"])
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    json_path.write_text(json.dumps(analysis_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = _markdown_report(message=message_payload, analysis=analysis_payload, json_ref=refs["json_ref"])
    markdown_path.write_text(markdown, encoding="utf-8")

    index = upsert_helpcenter_index_entry(
        index,
        analysis_id=analysis_id,
        message_id=message_id,
        status=str(analysis_payload.get("status") or "ready"),
        severity=str(message_payload.get("severity") or "warning"),
        source_kind=str(message_payload.get("source_kind") or ""),
        created_at=str(analysis_payload.get("generated_at") or _now_iso()),
        report_ref=refs["markdown_ref"],
        json_ref=refs["json_ref"],
        version=version,
        duplicate_of_analysis_id=duplicate_of,
    )
    index_path = write_helpcenter_index(index, repo_root=repo_root)
    return {
        "analysis_id": analysis_id,
        "message_id": message_id,
        "markdown_ref": refs["markdown_ref"],
        "json_ref": refs["json_ref"],
        "index_ref": str(index_path.relative_to(project_root)),
        "version": version,
        "duplicate_of_analysis_id": duplicate_of,
    }


def _report_stem(message: dict[str, Any], *, analysis_id: str, version: int) -> str:
    source_kind = str(message.get("source_kind") or "").strip()
    if source_kind == "github_workflow_failure":
        meta = dict(message.get("meta") or {})
        run_id = str(meta.get("run_id") or "").strip()
        job_id = str(meta.get("job_id") or "").strip()
        if run_id:
            job_part = f"-job-{job_id}" if job_id and job_id != "0" else ""
            return f"github-run-{run_id}{job_part}-v{version}"
    return f"{analysis_id}-v{version}"
