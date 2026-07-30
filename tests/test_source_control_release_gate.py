from __future__ import annotations

import pytest

from agent.services.source_control_release_gate import (
    REQUIRED_SOURCE_CONTROL_GATES,
    SourceControlReleaseGateError,
    evaluate_source_control_release_gate,
)


def _passed_evidence() -> list[dict[str, str]]:
    return [
        {
            "gate": gate,
            "status": "passed",
            "artifact_digest": f"{index + 1:064x}",
        }
        for index, gate in enumerate(REQUIRED_SOURCE_CONTROL_GATES)
    ]


def test_production_remains_unverified_without_supplied_ids() -> None:
    report = evaluate_source_control_release_gate(_passed_evidence())

    assert report.release_allowed is False
    assert report.production_verification.status == "unverified"
    assert report.production_verification.source_id is None
    assert report.production_verification.run_id is None


def test_missing_gate_is_fail_closed() -> None:
    report = evaluate_source_control_release_gate(_passed_evidence()[:-1])

    assert report.release_allowed is False
    assert report.missing_gates == (REQUIRED_SOURCE_CONTROL_GATES[-1],)


def test_duplicate_or_unknown_evidence_is_rejected() -> None:
    evidence = _passed_evidence()
    with pytest.raises(SourceControlReleaseGateError):
        evaluate_source_control_release_gate([*evidence, evidence[0]])
    with pytest.raises(SourceControlReleaseGateError):
        evaluate_source_control_release_gate(
            [
                {
                    "gate": "invented",
                    "status": "passed",
                    "artifact_digest": "a" * 64,
                }
            ]
        )


def test_production_verification_rejects_non_grounded_id_shapes() -> None:
    with pytest.raises(SourceControlReleaseGateError, match="SRC_\\*/RUN_\\*"):
        evaluate_source_control_release_gate(
            _passed_evidence(),
            production_payload={
                "status": "passed",
                "source_id": "source-example",
                "run_id": "run-example",
                "artifact_digest": "f" * 64,
            },
        )
