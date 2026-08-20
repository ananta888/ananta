from __future__ import annotations

from pathlib import Path

from agent.services.tiny_router.benchmark import (
    BenchmarkRunner, dataset_provenance, load_benchmark_cases,
)
from agent.services.tiny_router.profiles import ProfileCatalog
from agent.services.tool_schema_adapter_service import ToolSchemaAdapterService

ROOT = Path(__file__).resolve().parents[3]


def test_committed_benchmark_is_reproducible_and_passes_quality_gate():
    path = ROOT / "benchmarks" / "tiny_tool_router" / "cases.v1.json"
    cases = load_benchmark_cases(path)
    profile = ProfileCatalog.load().get("functiongemma-270m")
    tools = ToolSchemaAdapterService().get_openai_tools(
        ["repo.list_files", "codecompass.search", "git.status"]
    )
    report = BenchmarkRunner().run(cases, tools=tools, profile=profile)
    assert report.selection_accuracy == 1.0
    assert report.argument_exact_match == 1.0
    assert report.abstention_recall == 1.0
    assert report.unsafe_acceptance_rate == 0.0
    assert report.total == 8


def test_dataset_provenance_is_stable_and_separates_evaluation():
    path = ROOT / "benchmarks" / "tiny_tool_router" / "cases.v1.json"
    first = dataset_provenance(path)
    second = dataset_provenance(path)
    assert first == second
    assert first["record_count"] == 8
    assert first["split"]["train"] + first["split"]["evaluation"] == 8
