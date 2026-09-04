#!/usr/bin/env python3
"""Fail closed on DSPy lock advisories outside the documented mitigation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {"CVE-2025-69872", "PYSEC-2026-2447"}


def validate(report: object) -> dict[str, object]:
    if not isinstance(report, dict) or not isinstance(report.get("dependencies"), list):
        raise RuntimeError("dspy_advisory_report_invalid")
    findings: list[tuple[str, str]] = []
    for dependency in report["dependencies"]:
        if not isinstance(dependency, dict) or not isinstance(dependency.get("vulns"), list):
            raise RuntimeError("dspy_advisory_report_invalid")
        name = str(dependency.get("name") or "").lower()
        for vulnerability in dependency["vulns"]:
            if not isinstance(vulnerability, dict):
                raise RuntimeError("dspy_advisory_report_invalid")
            identifiers = {str(vulnerability.get("id") or ""), *(str(v) for v in vulnerability.get("aliases") or ())}
            relevant = identifiers & ALLOWED
            if name != "diskcache" or not relevant:
                findings.append((name, ",".join(sorted(identifiers))))
    if findings:
        raise RuntimeError(f"dspy_unmitigated_dependency_advisory:{findings}")
    policy = json.loads((ROOT / "config/licenses/dspy-optimization.v1.json").read_text(encoding="utf-8"))
    diskcache = next(value for value in policy["direct_dependencies"] if value["name"] == "diskcache")
    if diskcache["security_status"] != "known_vulnerability_mitigated" or "disabled" not in diskcache["control"]:
        raise RuntimeError("dspy_advisory_mitigation_missing")
    return {"status": "passed", "unmitigated_findings": 0, "human_intervention_required": False}


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_dspy_dependency_advisories.py REPORT.json")
    print(json.dumps(validate(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
