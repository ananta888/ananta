"""Single-assignment entrypoint for the isolated verification Worker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ananta_contracts.verification import VerificationAssignmentV1
from worker.verification.adapters import (
    CrossHairCheckAdapter,
    CrossHairCoverAdapter,
    HypothesisCrossHairBackendAdapter,
    PytestHypothesisRunnerAdapter,
)
from worker.verification.target_policy import VerificationTargetPolicy


def execute_assignment(raw: dict, *, repository: Path) -> dict:
    assignment = VerificationAssignmentV1.from_mapping(raw)
    VerificationTargetPolicy(repository / "config/verification/property-catalog.v1.json").authorize(assignment)
    if assignment.backend == "hypothesis":
        report = PytestHypothesisRunnerAdapter().run(assignment, repository=repository)
    elif assignment.backend == "crosshair_backend":
        report = HypothesisCrossHairBackendAdapter().run(assignment, repository=repository)
    elif assignment.backend == "crosshair_check":
        report = CrossHairCheckAdapter().check(assignment, repository=repository)
    elif assignment.backend == "crosshair_cover":
        report = CrossHairCoverAdapter().check(assignment, repository=repository)
    else:
        raise ValueError("verification_diff_requires_explicit_symbol_pair")
    return report.to_dict()


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one Hub-bound verification assignment")
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = json.loads(args.assignment.read_text(encoding="utf-8"))
    report = execute_assignment(raw, repository=args.repository)
    output = args.output.resolve()
    workspace = args.assignment.resolve().parent
    if output.parent != workspace or output.is_symlink():
        raise ValueError("verification_output_outside_task_workspace")
    output.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
