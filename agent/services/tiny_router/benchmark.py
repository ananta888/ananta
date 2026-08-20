"""Deterministic evaluation helpers for candidate routing."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.services.tiny_router.preselection import AllowedToolPreselector
from agent.services.tiny_router.types import TinyActionModelProfile
from agent.services.tiny_router.validation import CandidateValidator

_SENSITIVE_PARTS = (
    "api_key", "authorization", "credential", "password", "secret", "token",
)


@dataclass(frozen=True)
class BenchmarkReport:
    total: int
    selection_accuracy: float
    argument_exact_match: float
    abstention_recall: float
    unsafe_acceptance_rate: float
    invalid_schema_rate: float
    escalation_rate: float
    catalog_sizes: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta.tiny_tool_router_benchmark.v1",
            "total": self.total,
            "selection_accuracy": self.selection_accuracy,
            "argument_exact_match": self.argument_exact_match,
            "abstention_recall": self.abstention_recall,
            "unsafe_acceptance_rate": self.unsafe_acceptance_rate,
            "invalid_schema_rate": self.invalid_schema_rate,
            "escalation_rate": self.escalation_rate,
            "catalog_sizes": [dict(item) for item in self.catalog_sizes],
        }

    def to_csv(self) -> str:
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["metric", "value"])
        for key, value in self.as_dict().items():
            if key not in {"schema", "catalog_sizes"}:
                writer.writerow([key, value])
        for row in self.catalog_sizes:
            writer.writerow(["catalog_size_" + str(row["requested"]), row["selected"]])
        return output.getvalue()


def load_benchmark_cases(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != "ananta.tiny_tool_router_cases.v1":
        raise ValueError("unsupported_benchmark_case_schema")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("benchmark_cases_must_be_array")
    assert_no_sensitive_fields(payload)
    return [dict(item) for item in cases]


def assert_no_sensitive_fields(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in _SENSITIVE_PARTS):
                raise ValueError("sensitive_dataset_field:" + path + "." + str(key))
            assert_no_sensitive_fields(item, path=path + "." + str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_sensitive_fields(item, path=f"{path}[{index}]")


def dataset_provenance(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    raw = source.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    assert_no_sensitive_fields(payload)
    cases = payload.get("cases") if isinstance(payload, Mapping) else None
    if not isinstance(cases, list):
        raise ValueError("dataset_cases_missing")
    split = {"train": 0, "evaluation": 0}
    for row in cases:
        case_id = str((row or {}).get("id") or "")
        bucket = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:8], 16)
        split["evaluation" if bucket % 5 == 0 else "train"] += 1
    return {
        "schema": "ananta.tiny_router_dataset_provenance.v1",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "record_count": len(cases),
        "split_algorithm": "sha256(case_id)-mod-5",
        "split": split,
    }


class BenchmarkRunner:
    def __init__(self) -> None:
        self._validator = CandidateValidator()
        self._selector = AllowedToolPreselector()

    def run(
        self, cases: Sequence[Mapping[str, Any]], *,
        tools: Sequence[Mapping[str, Any]], profile: TinyActionModelProfile,
    ) -> BenchmarkReport:
        selected = exact = abstain_expected = abstain_correct = 0
        unsafe_accepted = invalid = escalated = 0
        for case in cases:
            result = self._validator.validate(
                case.get("model_output"), tools=tools, profile=profile,
                adapter_id="replay",
            )
            expected = case.get("expected") or {}
            expected_tool = expected.get("tool_name")
            if expected_tool is None:
                abstain_expected += 1
                if result.status in {"abstain", "invalid"}:
                    abstain_correct += 1
                if result.candidate:
                    unsafe_accepted += 1
            elif result.candidate and result.candidate.tool_name == expected_tool:
                selected += 1
                if dict(result.candidate.arguments) == expected.get("arguments", {}):
                    exact += 1
            if result.status == "invalid":
                invalid += 1
                escalated += 1
        positive = sum(
            1 for case in cases
            if (case.get("expected") or {}).get("tool_name") is not None
        )
        total = len(cases)
        return BenchmarkReport(
            total,
            round(selected / positive, 4) if positive else 1.0,
            round(exact / positive, 4) if positive else 1.0,
            round(abstain_correct / abstain_expected, 4) if abstain_expected else 1.0,
            round(unsafe_accepted / total, 4) if total else 0.0,
            round(invalid / total, 4) if total else 0.0,
            round(escalated / total, 4) if total else 0.0,
            self._catalog_size_results(tools),
        )

    def _catalog_size_results(
        self, tools: Sequence[Mapping[str, Any]],
    ) -> tuple[Mapping[str, Any], ...]:
        base = list(tools)
        rows: list[Mapping[str, Any]] = []
        for requested in (5, 20, 50, 100):
            expanded = list(base)
            while len(expanded) < requested:
                index = len(expanded)
                expanded.append({
                    "type": "function",
                    "function": {
                        "name": f"benchmark.distractor.{index:03d}",
                        "description": "Evaluation-only distractor",
                        "parameters": {
                            "type": "object", "properties": {},
                            "additionalProperties": False,
                        },
                    },
                })
            selected = self._selector.select(
                "search repository", expanded[:requested], top_k=5,
            )
            rows.append({
                "requested": requested, "available": len(expanded[:requested]),
                "selected": len(selected),
            })
        return tuple(rows)
