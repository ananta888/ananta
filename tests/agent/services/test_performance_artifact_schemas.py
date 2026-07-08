import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).parents[3]


@pytest.mark.parametrize(
    ("schema_path", "example_path"),
    [
        (
            "docs/schemas/benchmark_run_artifact.v1.schema.json",
            "tests/baselines/schemas/benchmark_run_artifact.v1.json",
        ),
        (
            "docs/schemas/performance_comparison_artifact.v1.schema.json",
            "tests/baselines/schemas/performance_comparison_artifact.v1.json",
        ),
    ],
)
def test_performance_artifact_schema_examples(schema_path, example_path):
    schema = json.loads((ROOT / schema_path).read_text(encoding="utf-8"))
    example = json.loads((ROOT / example_path).read_text(encoding="utf-8"))
    jsonschema.validate(example, schema)


def test_hypothesis_schema_rejects_missing_evidence():
    schema_path = ROOT / "docs/schemas/optimization_hypothesis_artifact.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    payload = {
        "schema": "optimization_hypothesis_artifact.v1",
        "hypothesis_id": "h",
        "hotspot_refs": [],
        "suspected_bottleneck": "unknown",
        "expected_effect": "measure more",
        "affected_files": [],
        "risk": "medium",
        "required_measurements": ["profile"],
        "falsification_criteria": ["no hotspot"],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)
