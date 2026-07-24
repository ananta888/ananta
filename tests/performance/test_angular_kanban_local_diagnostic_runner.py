from __future__ import annotations

import json

import pytest

from scripts.performance.run_angular_kanban_local_diagnostic import (
    AngularDiagnosticError,
    MARKER,
    parse_diagnostic_marker,
    validate_local_report,
)


def _report() -> dict:
    return {
        "schema": "ananta.kanban-performance-local-diagnostic.v1",
        "evidence_classification": "local_diagnostic_not_release_evidence",
        "formal": False,
        "release_evidence": False,
        "dataset": {
            "cards": 1000,
            "view_groups": 10,
            "status_groups": 10,
            "canonical_columns": 4,
        },
        "viewports": [{"viewport": {"name": "desktop"}}, {"viewport": {"name": "mobile"}}],
    }


def test_parser_extracts_exactly_one_real_playwright_marker() -> None:
    report = _report()
    parsed = parse_diagnostic_marker(
        f"playwright output\n{MARKER}{json.dumps(report)}\n1 passed\n"
    )

    assert parsed == report


def test_parser_rejects_ambiguous_markers() -> None:
    encoded = json.dumps(_report())

    with pytest.raises(AngularDiagnosticError, match="marker_count_invalid"):
        parse_diagnostic_marker(f"{MARKER}{encoded}\n{MARKER}{encoded}")


def test_local_diagnostic_cannot_claim_formal_evidence() -> None:
    report = _report()
    report["formal"] = True

    with pytest.raises(AngularDiagnosticError, match="must_remain_local"):
        validate_local_report(report)
