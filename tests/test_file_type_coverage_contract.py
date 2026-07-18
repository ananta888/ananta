from __future__ import annotations

import pytest

from ananta_contracts.file_type_coverage import CoverageOutcome, FileTypeCoverageReport
from ananta_contracts.file_type_support import load_file_type_support_registry


@pytest.fixture
def registry():
    from pathlib import Path

    return load_file_type_support_registry(Path(__file__).resolve().parents[1])


def test_coverage_report_records_every_outcome_and_aggregates_deterministically(registry):
    report = FileTypeCoverageReport(registry, pipeline="setup_index")
    python = registry.descriptor("python")
    markdown = registry.descriptor("markdown")
    report.add(
        path="src/app.py",
        descriptor=python,
        outcome=CoverageOutcome.INDEXED,
        byte_size=10,
        duration_seconds=0.25,
        symbol_count=2,
        edge_count=1,
        fallback_reason="parser_fallback",
    )
    report.add(
        path="README.md",
        descriptor=markdown,
        outcome=CoverageOutcome.EXCLUDED,
        exclusion_reason="max_files_fair_share",
        diagnostics=("selection_limit",),
        byte_size=20,
    )
    report.add(
        path="notes.unknown",
        descriptor=None,
        detected_type="unknown_text",
        outcome=CoverageOutcome.UNSUPPORTED,
        diagnostics=("unknown_text_type",),
        byte_size=5,
    )

    payload = report.as_dict()

    assert [item["path"] for item in payload["files"]] == ["README.md", "notes.unknown", "src/app.py"]
    assert payload["aggregate"]["file_count"] == 3
    assert payload["aggregate"]["indexed_share"] == pytest.approx(1 / 3, abs=1e-6)
    assert payload["aggregate"]["unknown_text_count"] == 1
    assert payload["aggregate"]["by_exclusion_reason"]["max_files_fair_share"]["count"] == 1
    assert payload["aggregate"]["duration_seconds"] == pytest.approx(0.25)
    assert payload["aggregate"]["symbol_count"] == 2
    assert payload["aggregate"]["edge_count"] == 1
    assert payload["aggregate"]["by_fallback"]["parser_fallback"]["count"] == 1
    python_snapshot = next(
        item for item in report.metrics_snapshot() if item["format_id"] == "python"
    )
    assert python_snapshot == {
        "format_id": "python",
        "file_count": 1,
        "byte_size": 10,
        "outcomes": {"indexed": 1},
        "diagnostics": {},
        "fallbacks": {"parser_fallback": 1},
        "duration_seconds_by_outcome": {"indexed": 0.25},
        "symbol_count": 2,
        "edge_count": 1,
    }
    assert report.manifest_coverage() == {
        "manifest_candidate_count": 3,
        "indexed": 1,
        "excluded": 1,
        "unsupported": 1,
        "failed": 0,
        "truncated": False,
        "diagnostic_counts": {"selection_limit": 1, "unknown_text_type": 1},
    }


def test_coverage_support_level_is_derived_from_effective_pipeline_truth(registry):
    report = FileTypeCoverageReport(registry, pipeline="repository_map")
    descriptor = registry.descriptor("python")

    record = report.add(
        path="agent/app.py",
        descriptor=descriptor,
        outcome="indexed",
        byte_size=1,
    )

    assert record.support_level in {"text_index", "symbol_index", "semantic_graph", "domain_parser"}
    assert record.detected_type == "python"


@pytest.mark.parametrize("path", ["/etc/passwd", "../secret.env", "src/../../secret"])
def test_coverage_rejects_non_repository_relative_paths(registry, path):
    report = FileTypeCoverageReport(registry, pipeline="setup_index")

    with pytest.raises(ValueError, match="coverage_path_must_be_repository_relative"):
        report.add(
            path=path,
            descriptor=registry.descriptor("python"),
            outcome="indexed",
            byte_size=1,
        )


def test_coverage_requires_explicit_reason_for_exclusion(registry):
    report = FileTypeCoverageReport(registry, pipeline="setup_index")

    with pytest.raises(ValueError, match="coverage_exclusion_reason_required"):
        report.add(
            path="README.md",
            descriptor=registry.descriptor("markdown"),
            outcome="excluded",
            byte_size=1,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("duration_seconds", -0.1, "coverage_duration"),
        ("duration_seconds", float("inf"), "coverage_duration"),
        ("symbol_count", -1, "coverage_record_counts"),
        ("edge_count", -1, "coverage_record_counts"),
    ],
)
def test_coverage_rejects_invalid_operational_metrics(registry, field, value, message):
    kwargs = {field: value}
    with pytest.raises(ValueError, match=message):
        FileTypeCoverageReport(registry, pipeline="setup_index").add(
            path="src/app.py",
            descriptor=registry.descriptor("python"),
            outcome="indexed",
            byte_size=1,
            **kwargs,
        )
