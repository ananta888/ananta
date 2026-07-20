from __future__ import annotations

import json

from scripts.benchmark.acoustic_residual_privacy import DEFAULT_OUTPUT, expected_document


def test_acoustic_residual_attack_gate_is_current_content_free_and_fail_closed() -> None:
    expected = expected_document()
    assert json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8")) == expected
    assert expected["passed"] is True
    assert expected["decision"] == {
        "activation_allowed": False,
        "measured_verdict": "no_go",
        "ordinary_and_transcript_fallback_required": True,
        "production_policy_verdict": "no_go",
    }
    assert expected["measurements"]["membership_inference_score"] > expected["thresholds"][
        "maximum_membership_inference_score"
    ]
    serialized = json.dumps(expected).lower()
    assert "/home/" not in serialized
    assert "source_identifier" not in serialized
