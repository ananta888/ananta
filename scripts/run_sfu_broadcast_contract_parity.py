#!/usr/bin/env python3
"""Run the shared SFU contract corpus against Python and TypeScript."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.sfu_broadcast_contract_validator import (  # noqa: E402
    SfuBroadcastCorpusVerifier,
)
from scripts.sfu_broadcast_gate_common import (  # noqa: E402
    atomic_write_report,
    canonical_sha256,
    read_bounded_json,
    run_bounded_command,
)

DEFAULT_CORPUS = ROOT / "tests/contracts/sfu_broadcast/corpus.v1.json"
DEFAULT_OUTPUT = ROOT / "artifacts/test-gates/sfu-broadcast-contract-parity.json"


class RepositoryArtifacts:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def read_bytes(self, relative_path: str) -> bytes:
        candidate = (self._root / relative_path).resolve()
        candidate.relative_to(self._root)
        if candidate.is_symlink() or not candidate.is_file():
            raise FileNotFoundError(relative_path)
        return candidate.read_bytes()


def evaluate_runtime_reports(
    python_report: Mapping[str, Any],
    typescript_report: Mapping[str, Any],
) -> tuple[str, tuple[str, ...], dict[str, int]]:
    reasons: set[str] = set()
    python_total = int(python_report.get("tests", 0))
    python_failed = sum(
        int(python_report.get(name, 0))
        for name in ("failures", "errors", "skipped")
    )
    if python_total < 1 or python_failed != 0:
        reasons.add("contract_parity_python_failed_or_incomplete")
    typescript_total = int(typescript_report.get("numTotalTests", 0))
    typescript_passed = int(typescript_report.get("numPassedTests", 0))
    typescript_failed = int(typescript_report.get("numFailedTests", 0))
    typescript_pending = int(typescript_report.get("numPendingTests", 0))
    if (
        typescript_report.get("success") is not True
        or typescript_total < 1
        or typescript_passed != typescript_total
        or typescript_failed != 0
        or typescript_pending != 0
    ):
        reasons.add("contract_parity_typescript_failed_or_incomplete")
    return (
        "passed" if not reasons else "failed",
        tuple(sorted(reasons)),
        {
            "python_tests": python_total,
            "typescript_tests": typescript_total,
        },
    )


def _read_junit(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    if root.tag == "testsuite":
        suites = [root]
    else:
        suites = list(root.findall("testsuite"))
    return {
        name: sum(int(suite.attrib.get(name, "0")) for suite in suites)
        for name in ("tests", "failures", "errors", "skipped")
    }


def run(corpus_path: Path) -> dict[str, Any]:
    corpus = read_bounded_json(corpus_path)
    corpus_report = SfuBroadcastCorpusVerifier().inspect(
        corpus,
        RepositoryArtifacts(ROOT),
    )
    reasons = {
        issue.code for issue in corpus_report.integrity_issues
    }
    reasons.update(corpus_report.fail_closed_blockers)
    environment = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix="sfu-contract-parity-") as directory:
        temporary = Path(directory)
        python_xml = temporary / "python.xml"
        typescript_json = temporary / "typescript.json"
        python_command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_sfu_broadcast_contract_validator.py",
            "tests/test_sfu_broadcast_contract_materialization.py",
            "tests/fuzz/test_sfu_broadcast_duplicate_key_fuzz.py",
            f"--junitxml={python_xml}",
        ]
        typescript_command = [
            "npm",
            "--prefix",
            "frontend-angular",
            "run",
            "test:unit",
            "--",
            "src/app/services/sfu-broadcast-contract-validator.service.spec.ts",
            "--reporter=json",
            f"--outputFile={typescript_json}",
        ]
        python_result = run_bounded_command(
            python_command,
            cwd=ROOT,
            env=environment,
            timeout_seconds=300,
            cpu_seconds_max=300,
            memory_bytes_max=2147483648,
        )
        typescript_result = run_bounded_command(
            typescript_command,
            cwd=ROOT,
            env=environment,
            timeout_seconds=600,
            cpu_seconds_max=600,
            memory_bytes_max=3221225472,
        )
        if python_result.exit_code != 0 or python_result.timed_out:
            reasons.add("contract_parity_python_command_failed")
        if typescript_result.exit_code != 0 or typescript_result.timed_out:
            reasons.add("contract_parity_typescript_command_failed")
        try:
            python_report = _read_junit(python_xml)
        except (OSError, ET.ParseError, ValueError):
            python_report = {}
            reasons.add("contract_parity_python_report_invalid")
        try:
            typescript_report = read_bounded_json(typescript_json)
        except Exception:
            typescript_report = {}
            reasons.add("contract_parity_typescript_report_invalid")
        status, report_reasons, counts = evaluate_runtime_reports(
            python_report,
            typescript_report,
        )
        reasons.update(report_reasons)
    return {
        "schema": "ananta.sfu-broadcast-contract-parity.v1",
        "gate_id": "SFB-GATE-002",
        "status": "passed" if status == "passed" and not reasons else "failed",
        "activation_allowed": False,
        "reason_codes": sorted(reasons),
        "corpus": {
            "schema_version": corpus_report.version.schema_version,
            "corpus_version": corpus_report.version.corpus_version,
            "corpus_digest": corpus_report.version.corpus_digest,
            "fixture_count": corpus_report.fixture_count,
        },
        "runtime_counts": counts,
        "commands": {
            "python_sha256": canonical_sha256(python_command),
            "typescript_sha256": canonical_sha256(typescript_command),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = run(args.corpus)
    atomic_write_report(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
