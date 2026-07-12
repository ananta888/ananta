"""CLI that turns measured benchmark JSON into validated release evidence.

Usage:
    python -m benchmarks.run_release_benchmark \
      --input measurements.json --thresholds benchmarks/thresholds.voice-core.v1.json \
      --output artifacts/test-gates/voice-benchmark.json

The input is measurement evidence, not an instruction to execute models.  Model
execution remains in the isolated runtimes and hardware harnesses.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from benchmarks.release_report import (
    BenchmarkReport,
    BenchmarkReportBuilder,
    ExecutionEvidence,
    ModelEvidence,
    ThresholdSet,
)


def build_report(raw: Mapping[str, Any], threshold_raw: Mapping[str, Any]) -> BenchmarkReport:
    raw_execution = raw.get("execution")
    if not isinstance(raw_execution, Mapping):
        raise ValueError("execution evidence must be an object")
    models = raw_execution.get("models")
    if not isinstance(models, list) or not models or not all(isinstance(item, Mapping) for item in models):
        raise ValueError("execution models must be a non-empty list of objects")
    execution = ExecutionEvidence(
        git_sha=str(raw_execution.get("git_sha") or ""),
        engine_versions=_mapping(raw_execution.get("engine_versions"), "engine_versions"),
        profile_id=str(raw_execution.get("profile_id") or ""),
        hardware=_mapping(raw_execution.get("hardware"), "hardware"),
        configuration=_mapping(raw_execution.get("configuration"), "configuration"),
        models=tuple(
            ModelEvidence(
                capability=str(item.get("capability") or ""),
                engine=str(item.get("engine") or ""),
                model_id=str(item.get("model_id") or ""),
                model_revision=str(item.get("model_revision") or ""),
                manifest_digest=str(item.get("manifest_digest") or ""),
                quantization=str(item.get("quantization") or ""),
                execution_location=str(item.get("execution_location") or ""),
            )
            for item in models
        ),
    )
    return BenchmarkReportBuilder().build(
        report_id=str(raw.get("report_id") or ""),
        suite_id=str(raw.get("suite_id") or ""),
        dataset_id=str(raw.get("dataset_id") or ""),
        dataset_digest=str(raw.get("dataset_digest") or ""),
        dataset_split=str(raw.get("dataset_split") or ""),
        execution=execution,
        thresholds=ThresholdSet.from_mapping(threshold_raw),
        metrics=_mapping(raw.get("metrics"), "metrics"),
        baseline_metrics=_mapping(raw.get("baseline_metrics", {}), "baseline_metrics"),
        promotion_subject=str(raw.get("promotion_subject") or "baseline"),
    )


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _load_object(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--thresholds", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        report = build_report(_load_object(arguments.input), _load_object(arguments.thresholds))
        payload = {**report.as_dict(), "report_digest": report.digest}
        _write_atomic(arguments.output, payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0 if report.status == "passed" else 1


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI test
    raise SystemExit(main())
