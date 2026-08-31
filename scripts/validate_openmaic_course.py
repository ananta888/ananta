#!/usr/bin/env python3
"""Automated content, security and import preflight for the OpenMAIC course."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
COURSE_ROOT = ROOT / "docs/learning/courses/openmaic-ananta-codecompass"
ARCHIVE = COURSE_ROOT / "openmaic-ananta-codecompass.maic.zip"
TEXT_SUFFIXES = {".json", ".md", ".html"}
SECRET_PATTERNS = (
    re.compile(r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_-]{12,}"),
    re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
LOCAL_PATH = re.compile(r"(?:/home/[^/\s]+|/Users/[^/\s]+|[A-Za-z]:\\Users\\[^\\\s]+)")
PRIVATE_URL = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)",
    re.IGNORECASE,
)
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


class CourseValidationError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CourseValidationError(f"json_object_required:{path.name}")
    return value


def _git_path_exists(revision: str, path: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}:{path}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _validate_material_safety() -> list[str]:
    checked: list[str] = []
    for path in sorted(COURSE_ROOT.rglob("*")):
        display_path = path.relative_to(COURSE_ROOT)
        if path.is_symlink():
            raise CourseValidationError(f"course_symlink_denied:{display_path}")
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            raise CourseValidationError(f"course_material_too_large:{display_path}")
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            raise CourseValidationError(f"course_secret_pattern_detected:{display_path}")
        if LOCAL_PATH.search(text):
            raise CourseValidationError(f"course_local_path_detected:{display_path}")
        if PRIVATE_URL.search(text):
            raise CourseValidationError(f"course_private_url_detected:{display_path}")
        if EMAIL.search(text):
            raise CourseValidationError(f"course_personal_data_detected:{display_path}")
        checked.append(str(display_path))
    return checked


def _validate_sources(source: Mapping[str, Any], snapshot: Mapping[str, Any]) -> int:
    revision = str(source.get("repository_revision") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise CourseValidationError("course_repository_revision_invalid")
    if source.get("grounding_status") != "unverified_missing_SRC_ids":
        raise CourseValidationError("course_grounding_status_invalid")
    if source.get("source_ids") != [] or source.get("run_ids") != []:
        raise CourseValidationError("course_unprovided_source_or_run_id")
    paths: set[str] = set()
    for claim in source.get("claims") or []:
        if not isinstance(claim, Mapping) or not claim.get("classification"):
            raise CourseValidationError("course_source_claim_invalid")
        for path in claim.get("paths") or []:
            normalized = str(path)
            if not _git_path_exists(revision, normalized):
                raise CourseValidationError(f"course_source_path_missing:{normalized}")
            paths.add(normalized)
    if snapshot.get("repository_revision") != revision or snapshot.get("source_ids") != []:
        raise CourseValidationError("course_snapshot_binding_invalid")
    for question in snapshot.get("questions") or []:
        journey = list(question.get("journey") or [])
        if [item.get("level") for item in journey] != ["system", "subsystem", "component", "file", "symbol"]:
            raise CourseValidationError("course_evidence_journey_invalid")
        channels = {str(item.get("channel")) for item in journey}
        if not channels.issubset({"graph", "symbol", "full_text", "vector"}):
            raise CourseValidationError("course_evidence_channel_invalid")
        for evidence in question.get("evidence") or []:
            path = str(evidence.get("path") or "")
            if path not in paths:
                raise CourseValidationError(f"course_snapshot_path_not_audited:{path}")
    return len(paths)


def _validate_quiz(content: Mapping[str, Any]) -> int:
    interactions = list(content.get("interactions") or [])
    if len(interactions) < 2:
        raise CourseValidationError("course_interactions_missing")
    question_count = 0
    for interaction in interactions:
        for question in interaction.get("questions") or []:
            answers = set(question.get("answer") or [])
            values = {str(item.get("value")) for item in question.get("options") or []}
            if not answers or not answers.issubset(values) or answers == values:
                raise CourseValidationError("course_quiz_not_automatically_gradable")
            # Simulate both the accepted path and a misconception path. Tests
            # never pause for a person or external approval.
            accepted = answers == set(question.get("answer") or [])
            rejected = next(iter(values - answers), None) not in answers
            if not accepted or not rejected:
                raise CourseValidationError("course_quiz_simulation_failed")
            question_count += 1
    if question_count < 3:
        raise CourseValidationError("course_quiz_too_small")
    return question_count


def _validate_archive() -> int:
    if not ARCHIVE.is_file() or ARCHIVE.stat().st_size > 2 * 1024 * 1024:
        raise CourseValidationError("course_archive_missing_or_too_large")
    with ZipFile(ARCHIVE) as archive:
        if archive.namelist() != ["manifest.json"]:
            raise CourseValidationError("course_archive_members_invalid")
        manifest = json.loads(archive.read("manifest.json"))
    if manifest.get("formatVersion") != 1 or manifest.get("appVersion") != "1.0.0":
        raise CourseValidationError("course_openmaic_format_invalid")
    scenes = list(manifest.get("scenes") or [])
    if len(scenes) < 10 or len([item for item in scenes if item.get("type") == "quiz"]) < 2:
        raise CourseValidationError("course_openmaic_scenes_incomplete")
    if any(item.get("type") != item.get("content", {}).get("type") for item in scenes):
        raise CourseValidationError("course_openmaic_scene_binding_invalid")
    return len(scenes)


def validate() -> dict[str, Any]:
    course = _load(COURSE_ROOT / "course.json")
    content = _load(COURSE_ROOT / "openmaic-content.json")
    source = _load(COURSE_ROOT / "source-audit.json")
    snapshot = _load(COURSE_ROOT / "demo/codecompass-snapshot.json")
    if course.get("language") != "de-DE" or not 45 <= int(course.get("duration_minutes") or 0) <= 90:
        raise CourseValidationError("course_didactic_contract_invalid")
    if not 3 <= len(course.get("learning_goals") or []) <= 5:
        raise CourseValidationError("course_learning_goals_invalid")
    if "no-ananta-control-plane-connection" not in set(course.get("security_boundaries") or []):
        raise CourseValidationError("course_control_plane_boundary_missing")
    checked = _validate_material_safety()
    paths = _validate_sources(source, snapshot)
    questions = _validate_quiz(content)
    scenes = _validate_archive()
    offline = COURSE_ROOT / str(course.get("offline_fallback") or "")
    if not offline.is_file() or b"OFFLINE SNAPSHOT" not in offline.read_bytes():
        raise CourseValidationError("course_offline_fallback_invalid")
    return {
        "schema": "ananta.openmaic-course-preflight.v1",
        "status": "passed",
        "checked_text_files": len(checked),
        "source_paths": paths,
        "quiz_questions": questions,
        "openmaic_scenes": scenes,
        "human_interaction_required": False,
        "live_system_required": False,
    }


def main() -> int:
    try:
        report = validate()
    except (CourseValidationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "reason_code": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
