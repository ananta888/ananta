#!/usr/bin/env python3
"""Execute real Visual Process Assistant suites and emit fail-closed evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.generate_visual_process_assistant_gates import (  # noqa: E402
    FUNCTIONAL_EVIDENCE_INPUT,
    FUNCTIONAL_EVIDENCE_SCHEMA,
    FUNCTIONAL_OUTPUT,
    FUNCTIONAL_SUITES,
    build_functional_report,
    canonical_bytes,
    functional_source_hashes,
    functional_source_revision,
)
from scripts.visual_process_test_authority import (  # noqa: E402
    AUTHORIZED_SOURCE_ID_ENV,
    AUTHORIZED_SOURCE_IDS_ENV,
    hub_preauthorized_test_environment,
)

ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TEST_SUMMARY = re.compile(r"(?<!\d)(\d+)\s+(passed|failed)\b")
VITEST_TEST_LINE = re.compile(r"^\s*Tests\s+(.+)$", re.MULTILINE)


def _implementation_available(spec: Mapping[str, Any]) -> bool:
    paths = [ROOT / str(path) for path in spec.get("implementation_paths") or []]
    return bool(paths) and all(path.is_file() for path in paths)


def _executed_test_count(output: str) -> int:
    clean = ANSI_ESCAPE.sub("", output)
    vitest = VITEST_TEST_LINE.search(clean)
    if vitest:
        return sum(
            int(count) for count, state in TEST_SUMMARY.findall(vitest.group(1)) if state in {"passed", "failed"}
        )
    return sum(int(count) for count, _state in TEST_SUMMARY.findall(clean))


def _run_suite(spec: Mapping[str, Any]) -> dict[str, Any] | None:
    if not _implementation_available(spec):
        return None
    if spec.get("evidence_mode") == "positive_source_authority":
        environment = hub_preauthorized_test_environment(os.environ)
        singular = str(environment.get(AUTHORIZED_SOURCE_ID_ENV) or "").strip()
        plural = str(environment.get(AUTHORIZED_SOURCE_IDS_ENV) or "")
        provided_source_ids = [singular] if singular else []
        provided_source_ids.extend(item.strip() for item in plural.split(",") if item.strip())
        with tempfile.TemporaryDirectory(prefix="ananta-vpa-authority-") as directory:
            gate_path = Path(directory) / "codecompass-positive.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/generate_codecompass_e2e_gate.py"),
                    "--positive-authority",
                    "--output",
                    str(gate_path),
                ],
                cwd=ROOT,
                env={**environment, "PYTHONHASHSEED": "0"},
                text=True,
                capture_output=True,
                timeout=300,
                check=False,
            )
            if completed.returncode != 0 or not gate_path.is_file():
                return {
                    "status": "failed",
                    "test_count": 1,
                    "reason_code": "positive_authority_gate_failed",
                    "evidence_paths": sorted(
                        str(path).replace("\\", "/") for path in spec.get("implementation_paths") or []
                    ),
                }
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
        grounding = dict(gate.get("source_grounding") or {})
        passed = (
            gate.get("release_allowed") is True
            and grounding.get("status") == "verified"
            and grounding.get("grounded_claims_released") is True
            and int(grounding.get("provided_source_count") or 0) == len(provided_source_ids)
            and grounding.get("source_ids_synthesized") is False
        )
        return {
            "status": "passed" if passed else "failed",
            "test_count": 1,
            "evidence_paths": sorted(str(path).replace("\\", "/") for path in spec.get("implementation_paths") or []),
            **({} if passed else {"reason_code": "positive_authority_gate_failed"}),
        }
    command = [str(value) for value in spec["reproduce"]]
    if command[0] == "python":
        command[0] = sys.executable
    working_directory = ROOT / str(spec.get("working_directory") or ".")
    environment = {**os.environ, "PYTHONHASHSEED": "0"}
    if str(spec["suite_id"]) in {
        "hub_worker_codecompass_integration",
        "feature_flag_rollback",
    }:
        environment["RUN_INTEGRATION_TESTS"] = "1"
    result = subprocess.run(
        command,
        cwd=working_directory,
        env=environment,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )
    output = result.stdout + result.stderr
    count = _executed_test_count(output)
    if count <= 0:
        print(
            f"visual_process_functional_suite_no_test_summary:{spec['suite_id']}",
            file=sys.stderr,
        )
        return None
    passed = result.returncode == 0
    return {
        "status": "passed" if passed else "failed",
        "test_count": count,
        "evidence_paths": sorted(str(path).replace("\\", "/") for path in spec.get("implementation_paths") or []),
        **({} if passed else {"reason_code": "test_failure"}),
    }


def build_functional_evidence(
    *,
    suite_ids: set[str] | None = None,
) -> dict[str, Any]:
    known_ids = {str(spec["suite_id"]) for spec in FUNCTIONAL_SUITES}
    selected = known_ids if suite_ids is None else set(suite_ids)
    unknown = selected - known_ids
    if unknown:
        raise ValueError(f"visual_process_assistant_unknown_functional_suite:{sorted(unknown)[0]}")
    results: dict[str, Any] = {}
    for spec in FUNCTIONAL_SUITES:
        suite_id = str(spec["suite_id"])
        if suite_id not in selected:
            continue
        result = _run_suite(spec)
        if result is not None:
            results[suite_id] = result
    return {
        "schema": FUNCTIONAL_EVIDENCE_SCHEMA,
        "source_revision": functional_source_revision(),
        "source_hashes": functional_source_hashes(),
        "results": results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", action="append", dest="suite_ids")
    parser.add_argument("--evidence-output", type=Path, default=FUNCTIONAL_EVIDENCE_INPUT)
    parser.add_argument("--report-output", type=Path, default=FUNCTIONAL_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        evidence = build_functional_evidence(suite_ids=set(arguments.suite_ids) if arguments.suite_ids else None)
        report = build_functional_report(evidence)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    arguments.evidence_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.evidence_output.write_bytes(canonical_bytes(evidence))
    arguments.report_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.report_output.write_bytes(canonical_bytes(report))
    print(
        json.dumps(
            {
                "status": report["status"],
                "suites": {item["suite_id"]: item["status"] for item in report["suites"]},
            },
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
