from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = ROOT / "artifacts" / "e2e"
STATUS_VALUES = {"passed", "failed", "skipped", "advisory"}
_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\b(token|access_token|refresh_token|api[-_]?key|secret|password)\s*[:=]\s*([^\s,;]+)"),
        r"\1=<REDACTED>",
    ),
    (re.compile(r"\b[A-Za-z0-9]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "<REDACTED_JWT>"),
    (re.compile(r"\b(?:[a-f0-9]{32,}|[A-Za-z0-9_-]{32,})\b"), "<REDACTED_TOKEN>"),
    (
        re.compile(r"(?i)\b([A-Z]:\\(?:[^\\\n]+\\)*[^\\\n]*)"),
        "<REDACTED_PATH>",
    ),
    (
        re.compile(r"(?<!\w)/(?:home|Users|root|var|etc|opt|mnt|private)(?:/[^\s]+)+"),
        "<REDACTED_PATH>",
    ),
)


def _sanitize_id(raw: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(raw or "").strip())
    cleaned = cleaned.strip("-")
    return cleaned or "run"


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def new_run_id(prefix: str = "e2e") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{_sanitize_id(prefix)}-{ts}"


def flow_dir(run_id: str, flow_id: str, *, artifact_root: Path | None = None) -> Path:
    root = artifact_root or DEFAULT_ARTIFACT_ROOT
    target = root / _sanitize_id(run_id) / _sanitize_id(flow_id)
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_text_artifact(
    run_id: str,
    flow_id: str,
    file_name: str,
    content: str,
    *,
    artifact_root: Path | None = None,
) -> str:
    target_dir = flow_dir(run_id, flow_id, artifact_root=artifact_root)
    target_file = target_dir / _sanitize_id(file_name)
    target_file.write_text(str(content), encoding="utf-8")
    return _repo_relative(target_file)


def write_binary_artifact(
    run_id: str,
    flow_id: str,
    file_name: str,
    content: bytes,
    *,
    artifact_root: Path | None = None,
) -> str:
    target_dir = flow_dir(run_id, flow_id, artifact_root=artifact_root)
    target_file = target_dir / _sanitize_id(file_name)
    target_file.write_bytes(content)
    return _repo_relative(target_file)


def redact_sensitive_text(text: str) -> str:
    redacted = str(text or "")
    for pattern, replacement in _REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_text_list(values: list[str] | None) -> list[str]:
    return [redact_sensitive_text(str(item)) for item in list(values or [])]


def sanitize_report_payload(report: dict[str, Any]) -> dict[str, Any]:
    def _sanitize(value: Any) -> Any:
        if isinstance(value, str):
            return redact_sensitive_text(value)
        if isinstance(value, list):
            return [_sanitize(item) for item in value]
        if isinstance(value, dict):
            return {str(key): _sanitize(item) for key, item in value.items()}
        return value

    return _sanitize(report)


def summarize_flows(flow_entries: list[dict[str, Any]]) -> dict[str, int]:
    statuses = Counter(str(item.get("status", "")).lower() for item in flow_entries)
    blocking_failed = sum(1 for item in flow_entries if item.get("blocking") and item.get("status") == "failed")
    return {
        "total": len(flow_entries),
        "passed": statuses.get("passed", 0),
        "failed": statuses.get("failed", 0),
        "skipped": statuses.get("skipped", 0),
        "advisory": statuses.get("advisory", 0),
        "blocking_failed": blocking_failed,
    }


def make_flow_entry(
    *,
    flow_id: str,
    status: str,
    blocking: bool,
    logs: list[str] | None = None,
    snapshots: list[str] | None = None,
    screenshots: list[str] | None = None,
    videos: list[str] | None = None,
    trace_bundle_refs: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    normalized_status = str(status).strip().lower()
    if normalized_status not in STATUS_VALUES:
        raise ValueError(f"unsupported flow status: {status!r}")
    return {
        "flow_id": str(flow_id),
        "status": normalized_status,
        "blocking": bool(blocking),
        "logs": list(logs or []),
        "snapshots": list(snapshots or []),
        "screenshots": list(screenshots or []),
        "videos": list(videos or []),
        "trace_bundle_refs": list(trace_bundle_refs or []),
        "artifact_refs": list(artifact_refs or []),
        "notes": list(notes or []),
    }


def build_report(run_id: str, flow_entries: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize_flows(flow_entries)
    return {
        "schema": "e2e_report.v1",
        "run_id": _sanitize_id(run_id),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "flows": flow_entries,
        "summary": summary,
    }


def write_report(run_id: str, report: dict[str, Any], *, artifact_root: Path | None = None) -> str:
    root = artifact_root or DEFAULT_ARTIFACT_ROOT
    run_root = root / _sanitize_id(run_id)
    run_root.mkdir(parents=True, exist_ok=True)
    report_path = run_root / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return _repo_relative(report_path)


def compact_summary(report: dict[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    return (
        f"run_id={report.get('run_id')} total={summary.get('total', 0)} "
        f"passed={summary.get('passed', 0)} failed={summary.get('failed', 0)} "
        f"skipped={summary.get('skipped', 0)} advisory={summary.get('advisory', 0)} "
        f"blocking_failed={summary.get('blocking_failed', 0)}"
    )
