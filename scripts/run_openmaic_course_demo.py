#!/usr/bin/env python3
"""Render approved local CodeCompass demo results with deterministic fallback."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
OFFLINE_SNAPSHOT = (
    ROOT
    / "docs/learning/courses/openmaic-ananta-codecompass/demo/codecompass-snapshot.json"
)
EXPECTED_SCHEMA = "ananta.openmaic-codecompass-demo-snapshot.v1"


def _load(path: Path) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("course_demo_snapshot_unavailable")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or value.get("schema") != EXPECTED_SCHEMA:
        raise ValueError("course_demo_snapshot_invalid")
    if value.get("grounding_status") != "unverified_missing_SRC_ids" or value.get("source_ids") != []:
        raise ValueError("course_demo_snapshot_grounding_invalid")
    questions = value.get("questions")
    if not isinstance(questions, list) or len(questions) < 3:
        raise ValueError("course_demo_snapshot_incomplete")
    return value


def resolve_snapshot(candidate: Path | None) -> tuple[Mapping[str, Any], str, str | None]:
    if candidate is not None:
        try:
            return _load(candidate), "approved_local_snapshot", None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            reason = str(exc) if str(exc).startswith("course_demo_") else "course_demo_snapshot_invalid"
            return _load(OFFLINE_SNAPSHOT), "offline_fallback", reason
    return _load(OFFLINE_SNAPSHOT), "offline_fallback", "course_demo_live_snapshot_not_configured"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args(argv)
    snapshot, mode, fallback_reason = resolve_snapshot(args.snapshot)
    print(
        json.dumps(
            {
                "schema": "ananta.openmaic-course-demo-result.v1",
                "mode": mode,
                "fallback_reason": fallback_reason,
                "snapshot_label": snapshot["snapshot_label"],
                "repository_revision": snapshot["repository_revision"],
                "questions": [
                    {
                        "id": item["id"],
                        "question": item["question"],
                        "answer": item["evidence_answer"],
                        "paths": [entry["path"] for entry in item["evidence"]],
                    }
                    for item in snapshot["questions"]
                ],
                "network_accessed": False,
                "human_interaction_required": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
