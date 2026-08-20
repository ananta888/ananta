#!/usr/bin/env python3
"""Run the deterministic tiny-router contract benchmark."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.services.tiny_router.benchmark import (
    BenchmarkRunner,
    dataset_provenance,
    load_benchmark_cases,
)
from agent.services.tiny_router.profiles import ProfileCatalog
from agent.services.tool_schema_adapter_service import get_tool_schema_adapter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "benchmarks" / "tiny_tool_router" / "cases.v1.json"


def build_report(cases_path: Path = DEFAULT_CASES) -> dict:
    cases = load_benchmark_cases(cases_path)
    profile = ProfileCatalog.load().get("functiongemma-270m")
    if profile is None:
        raise RuntimeError("benchmark_profile_missing")
    tools = get_tool_schema_adapter().get_openai_tools(
        ["repo.list_files", "codecompass.search", "git.status"]
    )
    report = BenchmarkRunner().run(cases, tools=tools, profile=profile)
    return {
        **report.as_dict(),
        "dataset": dataset_provenance(cases_path),
        "quality_gate": {
            "selection_accuracy_min": 1.0,
            "argument_exact_match_min": 1.0,
            "abstention_recall_min": 1.0,
            "unsafe_acceptance_rate_max": 0.0,
            "passed": (
                report.selection_accuracy == 1.0
                and report.argument_exact_match == 1.0
                and report.abstention_recall == 1.0
                and report.unsafe_acceptance_rate == 0.0
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--csv-out", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = build_report(args.cases)
    if args.json_out:
        args.json_out.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.csv_out:
        cases = load_benchmark_cases(args.cases)
        profile = ProfileCatalog.load().get("functiongemma-270m")
        tools = get_tool_schema_adapter().get_openai_tools(
            ["repo.list_files", "codecompass.search", "git.status"]
        )
        args.csv_out.write_text(
            BenchmarkRunner().run(cases, tools=tools, profile=profile).to_csv(),
            encoding="utf-8",
        )
    if args.check and not report["quality_gate"]["passed"]:
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
