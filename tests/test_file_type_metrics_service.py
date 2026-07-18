from __future__ import annotations

from pathlib import Path

from agent.services.file_type_metrics_service import FileTypeMetricPorts, FileTypeMetricsService
from ananta_contracts.file_type_support import load_file_type_support_registry


class _Metric:
    def __init__(self):
        self.events = []
        self.current = None

    def labels(self, **labels):
        self.current = labels
        return self

    def inc(self, value=1):
        self.events.append(("inc", self.current, value))

    def observe(self, value):
        self.events.append(("observe", self.current, value))


def _service():
    values = [_Metric() for _ in range(7)]
    ports = FileTypeMetricPorts(*values)
    registry = load_file_type_support_registry(Path(__file__).resolve().parents[1])
    return FileTypeMetricsService(registry=registry, ports=ports), ports


def test_metrics_snapshot_uses_bounded_registry_and_diagnostic_labels():
    service, ports = _service()

    service.observe_snapshot(
        pipeline="setup_index",
        snapshot=[
            {
                "format_id": "python",
                "byte_size": 42,
                "outcomes": {"indexed": 2},
                "diagnostics": {"secret_value_redacted": 1, "arbitrary-user-value": 3},
                "fallbacks": {"parser_fallback": 1},
                "duration_seconds_by_outcome": {"indexed": 0.125},
                "symbol_count": 0,
                "edge_count": 0,
            }
        ],
    )

    assert ports.files.events[0] == (
        "inc",
        {"pipeline": "setup_index", "format_id": "python", "outcome": "indexed"},
        2,
    )
    diagnostic_labels = [event[1]["diagnostic_code"] for event in ports.diagnostics.events]
    assert diagnostic_labels == ["other", "secret_value_redacted"]
    assert ports.bytes.events[0][2] == 42
    assert ports.durations.events[0][2] == 0.125
    assert ports.symbols.events[0][2] == 0
    assert ports.edges.events[0][2] == 0
    assert ports.fallbacks.events[0][1]["reason_code"] == "parser_fallback"


def test_parser_metrics_capture_duration_fallback_symbols_and_edges():
    service, ports = _service()

    service.observe_parser_result(
        pipeline="semantic_translation",
        format_id="typescript",
        outcome="indexed",
        duration_seconds=0.25,
        byte_size=100,
        symbol_count=4,
        edge_count=3,
        fallback_reason="parser_fallback",
        diagnostics=("parser_timeout",),
    )

    assert ports.durations.events[0][2] == 0.25
    assert ports.symbols.events[0][2] == 4
    assert ports.edges.events[0][2] == 3
    assert ports.fallbacks.events[0][1]["reason_code"] == "parser_fallback"


def test_unknown_labels_collapse_to_other():
    service, ports = _service()

    service.observe_parser_result(
        pipeline="user-controlled",
        format_id="user-controlled",
        outcome="user-controlled",
        duration_seconds=0,
        byte_size=0,
        symbol_count=0,
        edge_count=0,
    )

    assert ports.files.events[0][1] == {
        "pipeline": "other",
        "format_id": "other",
        "outcome": "failed",
    }


def test_rag_helper_manifest_projects_per_file_parser_metrics_without_reading_paths():
    service, ports = _service()

    service.observe_rag_helper_manifest(
        {
            "files": [
                {
                    "file": "docs/README.md",
                    "size": 123,
                    "duration_ms": 25,
                    "output_record_count": 9,
                    "fallback": True,
                    "fallback_reason": "parser_fallback",
                    "stats": {
                        "detail_count": 4,
                        "relation_count": 3,
                        "diagnostic_codes": ["parser_limit_exceeded"],
                    },
                }
            ]
        }
    )

    assert ports.files.events[0][1] == {
        "pipeline": "rag_helper",
        "format_id": "markdown",
        "outcome": "indexed",
    }
    assert ports.durations.events[0][2] == 0.025
    assert ports.bytes.events[0][2] == 123
    assert ports.symbols.events[0][2] == 4
    assert ports.edges.events[0][2] == 3
    assert ports.diagnostics.events[0][1]["diagnostic_code"] == "parser_limit_exceeded"


def test_path_metrics_use_canonical_compound_and_pattern_classification():
    service, ports = _service()

    service.observe_path_result(
        pipeline="repository_map",
        path=".github/workflows/ci.yml",
        outcome="indexed",
        duration_seconds=0.01,
        byte_size=10,
        symbol_count=2,
        edge_count=0,
    )

    assert ports.files.events[0][1]["format_id"] == "github_actions"
