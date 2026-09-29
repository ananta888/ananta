from __future__ import annotations

from worker.retrieval.codecompass_context_explainer import build_relation_explanation


def test_context_explainer_produces_machine_and_human_readable_reason():
    explanation = build_relation_explanation(
        chunk={
            "source": "src/PaymentService.java",
            "metadata": {
                "record_id": "method:PaymentService.retryTimeout",
                "record_kind": "java_method",
                "expanded_from": "method:PaymentController.handlePayment",
                "relation_path": "calls_probable_target -> injects_dependency",
                "expansion_reason": "profile:bugfix_local",
            },
        }
    )

    assert "human" in explanation
    assert "machine" in explanation
    assert "calls" in explanation["human"].lower()
    assert explanation["machine"]["record_id"] == "method:PaymentService.retryTimeout"
    assert explanation["machine"]["relation_steps"] == ["calls_probable_target", "injects_dependency"]

