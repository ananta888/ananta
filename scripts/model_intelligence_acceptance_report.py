#!/usr/bin/env python3
"""Build a source-grounded OWMA acceptance report from JUnit XML."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


_SOURCE_ID = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9._:-]*$")
_RUN_ID = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in counts:
            counts[key] += int(suite.attrib.get(key, "0"))
    return counts


def _stable_junit_sha256(path: Path) -> str:
    """Hash the semantic JUnit document without volatile runner metadata."""

    root = ET.parse(path).getroot()
    for element in root.iter():
        for attribute in ("hostname", "time", "timestamp"):
            element.attrib.pop(attribute, None)
        element.attrib = dict(sorted(element.attrib.items()))
        element.tail = None
    return hashlib.sha256(ET.tostring(root, encoding="utf-8")).hexdigest()


def build_report(
    *,
    profile: str,
    junit_path: Path,
    source_ids: tuple[str, ...],
    run_ids: tuple[str, ...],
    tool_digest: str,
    container_digest: str | None,
) -> dict[str, object]:
    if profile not in {"core", "extended"}:
        raise ValueError("profile must be core or extended")
    invalid_sources = sorted(
        identifier
        for identifier in source_ids
        if _SOURCE_ID.fullmatch(identifier) is None
    )
    invalid_runs = sorted(
        identifier
        for identifier in run_ids
        if _RUN_ID.fullmatch(identifier) is None
    )
    counts = _junit_counts(junit_path)
    tests_passed = (
        counts["tests"] > 0
        and counts["failures"] == 0
        and counts["errors"] == 0
    )
    evidence_complete = bool(source_ids) and bool(run_ids)
    evidence_complete = evidence_complete and not invalid_sources and not invalid_runs
    if not tests_passed:
        status = "failed"
        reason_code = "model_intelligence_tests_failed"
    elif not evidence_complete:
        status = "unverified"
        reason_code = "model_intelligence_evidence_missing"
    else:
        status = "passed"
        reason_code = None
    return {
        "schema_version": "model_intelligence_acceptance.v1",
        "profile": profile,
        "status": status,
        "reason_code": reason_code,
        "release_allowed": status == "passed",
        "tests": counts,
        "junit_sha256": _stable_junit_sha256(junit_path),
        "tool_digest": tool_digest,
        "container_digest": container_digest,
        "source_ids": sorted(source_ids),
        "run_ids": sorted(run_ids),
        "invalid_source_ids": invalid_sources,
        "invalid_run_ids": invalid_runs,
        "codecompass_repository_gate_reused": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("core", "extended"), required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-id", action="append", default=[])
    parser.add_argument("--run-id", action="append", default=[])
    parser.add_argument("--tool-digest", required=True)
    parser.add_argument("--container-digest")
    parser.add_argument("--require-release-evidence", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    report = build_report(
        profile=args.profile,
        junit_path=args.junit,
        source_ids=tuple(args.source_id),
        run_ids=tuple(args.run_id),
        tool_digest=args.tool_digest,
        container_digest=args.container_digest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    if report["status"] == "failed":
        return 1
    if args.require_release_evidence and report["status"] != "passed":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
