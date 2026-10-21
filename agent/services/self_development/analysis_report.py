"""Worker-side producer of the analysis report the self-development gate verifies.

The Hub's quality gate reads ``.ananta/mutation-report.json`` and checks that
``code_analysis.analysis.sources`` are baseline-trusted ranges the backend really
observed (``verify_worker_analysis``). The mutation loop produces that report
itself; a backend that drives CodeCompass through the MCP endpoint only has its
observations in the tool responses.

This module turns those observations into the same report:

- :class:`ObservationRecorder` keeps, per task and path, the **first** observed
  range (first wins, because only the pre-write read can match the baseline).
- :func:`baseline_from_description` reads the revision-bound source bundle the
  Hub wrote into the task description — the worker-side projection of the export
  baseline.
- :func:`write_analysis_report` writes only when everything holds: no report
  yet, at least one observation whose sha256 equals that baseline, and a report
  that validates against ``WorkerAnalysisReport``. Anything else leaves the file
  untouched, so a missing or mismatched report fails the gate honestly instead of
  being papered over.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SOURCE_CONTEXT_MARKER = "Revision-bound source context (JSON data):"
_SOURCE_CONTEXT_END_MARKER = "\nOnly change these files:"
_MAX_ANSWER_CHARS = 4000

_LOCK = threading.Lock()
_OBSERVATIONS: dict[str, dict[str, dict[str, Any]]] = {}


def reset_observations(task_id: str | None = None) -> None:
    """Drop recorded ranges — one task, or all (tests and session boundaries)."""
    with _LOCK:
        if task_id is None:
            _OBSERVATIONS.clear()
        else:
            _OBSERVATIONS.pop(str(task_id), None)


def record_observation(
    *,
    task_id: str,
    path: str,
    line_start: int,
    line_end: int,
    file_sha256: str | None = None,
) -> None:
    """Record one observed range; the first observation of a path wins.

    First wins because a later read of a file the backend has already modified
    carries post-write bytes and could never satisfy the baseline check — keeping
    it would silently replace the only trustworthy observation.
    """
    task_key = str(task_id or "").strip()
    rel_path = str(path or "").strip()
    if not task_key or not rel_path:
        return
    try:
        start, end = int(line_start), int(line_end)
    except (TypeError, ValueError):
        return
    if start < 1 or end < start:
        return
    digest = str(file_sha256 or "").strip().lower()
    if digest and len(digest) != 64:
        digest = ""
    with _LOCK:
        seen = _OBSERVATIONS.setdefault(task_key, {})
        if rel_path in seen:
            return
        seen[rel_path] = {
            "path": rel_path,
            "file_sha256": digest,
            "line_start": start,
            "line_end": end,
        }


def _sha256_of(workspace: Path, rel_path: str) -> str:
    target = workspace / rel_path
    try:
        if not target.is_file() or target.is_symlink():
            return ""
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        return ""


def record_tool_result(result: Any, *, task_id: str, workspace: str | Path) -> None:
    """Record every ``file_excerpt`` range of one successful tool response.

    The sha256 is taken from the tool payload when the executor provided one and
    computed from the workspace otherwise, so a range the backend observed gets
    recorded even for CodeCompass responses that report no digest.
    """
    if not isinstance(result, dict) or result.get("status") != "ok":
        return
    workspace_path = Path(workspace)
    payload_digest = str((result.get("data") or {}).get("file_sha256") or "").strip()
    for entry in result.get("evidence") or []:
        if not isinstance(entry, dict):
            continue
        rel_path = str(entry.get("path") or "").strip()
        if not rel_path or "line_start" not in entry or "line_end" not in entry:
            continue
        digest = payload_digest if _is_digest(payload_digest) else _sha256_of(workspace_path, rel_path)
        record_observation(
            task_id=task_id,
            path=rel_path,
            line_start=entry.get("line_start"),
            line_end=entry.get("line_end"),
            file_sha256=digest,
        )


def _is_digest(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def baseline_from_description(description: Any) -> dict[str, str]:
    """``{path: sha256}`` of the revision-bound source bundle in a task description."""
    text = str(description or "")
    start = text.find(_SOURCE_CONTEXT_MARKER)
    if start < 0:
        return {}
    start += len(_SOURCE_CONTEXT_MARKER)
    end = text.find(_SOURCE_CONTEXT_END_MARKER, start)
    raw = text[start: end if end > start else None].strip()
    try:
        bundle = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(bundle, dict):
        return {}
    baseline: dict[str, str] = {}
    for entry in bundle.get("files") or []:
        if not isinstance(entry, dict):
            continue
        rel_path = str(entry.get("path") or "").strip()
        digest = str(entry.get("sha256") or "").strip().lower()
        if rel_path and _is_digest(digest):
            baseline[rel_path] = digest
    return baseline


def baseline_trusted_observations(task_id: str, baseline: dict[str, str]) -> list[dict[str, Any]]:
    """Observed ranges whose digest still equals the exported baseline bytes."""
    if not baseline:
        return []
    with _LOCK:
        recorded = list((_OBSERVATIONS.get(str(task_id)) or {}).values())
    return [entry for entry in recorded if baseline.get(entry["path"]) == entry["file_sha256"]]


def write_analysis_report(
    workspace: str | Path,
    *,
    task_id: str,
    description: str,
    answer: str,
) -> bool:
    """Write ``.ananta/mutation-report.json`` when the gate's conditions hold.

    Returns ``False`` (without touching the workspace) when there is already a
    report — the mutation loop owns its own — or when the observations, baseline
    or texts cannot support a report that would pass verification.
    """
    root = Path(workspace)
    report_path = root / ".ananta" / "mutation-report.json"
    if report_path.exists():
        return False

    baseline = baseline_from_description(description)
    observations = baseline_trusted_observations(task_id, baseline)
    if not observations:
        return False

    findings = str(answer or "").strip()[:_MAX_ANSWER_CHARS]
    if not findings:
        return False

    observed = [
        {
            "path": entry["path"],
            "file_sha256": entry["file_sha256"],
            "line_start": entry["line_start"],
            "line_end": entry["line_end"],
        }
        for entry in sorted(observations, key=lambda item: item["path"])
    ]
    report = {
        "schema": "ananta_worker_mutation_report.v1",
        "task_id": str(task_id),
        "outcome": "final_answer",
        "code_analysis": {
            "required": True,
            "status": "accepted",
            "codecompass_observed": True,
            "observed_sources": observed,
            "analysis": {
                "sources": observed,
                "findings": findings,
                "change_reasoning": findings,
                "test_reasoning": (
                    "Verification is delegated to the Hub execute step, which runs the task's "
                    "declared allowlisted command and gates this run on its exit status."
                ),
            },
        },
    }

    from agent.services.self_development.analysis_verification import WorkerAnalysisReport

    try:
        # Same entry point the Hub's verifier uses, so what is written is exactly
        # what `verify_worker_analysis` will accept (strict models, JSON arrays).
        WorkerAnalysisReport.model_validate_json(json.dumps(report, ensure_ascii=False))
    except Exception as exc:  # never write a report the gate could not read
        log.warning("analysis_report_invalid task=%s reason=%s", task_id, type(exc).__name__)
        return False

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        log.warning("analysis_report_write_failed task=%s", task_id)
        return False
    return True
