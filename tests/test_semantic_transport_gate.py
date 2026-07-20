from __future__ import annotations

import json

from scripts.run_semantic_transport_gate import ARTIFACT, FORBIDDEN_KEYS, _verify_persisted, expected_document


def test_semantic_transport_gate_is_current_measured_and_green() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    assert _verify_persisted(artifact)
    assert artifact["measurement_method"] == "monotonic_product_queue_and_executed_recovery_suites"
    assert artifact["measurements"]["saturation_samples"] >= 128
    assert artifact["measurements"]["timer_leaks"] == 0
    assert artifact["test_suites"] == {"python_exit_code": 0, "angular_exit_code": 0}


def test_live_product_queue_saturation_probe_meets_priority_budgets() -> None:
    measured = expected_document()
    assert measured["passed"] is True
    assert measured["checks"]["bulk_saturation_measured"] is True
    assert measured["measurements"]["control_sent"] == measured["measurements"]["saturation_samples"]
    assert measured["measurements"]["transcript_sent"] == measured["measurements"]["saturation_samples"]


def test_semantic_transport_gate_contains_no_content_fields() -> None:
    artifact = json.loads(ARTIFACT.read_text())

    def walk(value):
        if isinstance(value, dict):
            assert not (set(map(str.lower, value)) & FORBIDDEN_KEYS)
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(artifact)
