from __future__ import annotations

from worker.coding.semantic_output_correction import correct_semantic_enum_fields


def test_semantic_output_correction_repairs_near_enum_value() -> None:
    corrected, report = correct_semantic_enum_fields(
        payload={"risk_classification": "critcal"},
        policy={
            "enabled": True,
            "similarity_threshold": 0.8,
            "min_margin": 0.0,
            "lexical_weight": 1.0,
            "embedding_provider": {"provider": "local", "dimensions": 12},
            "fields": {"risk_classification": {"enabled": True, "candidates": ["low", "medium", "high", "critical"]}},
        },
    )

    assert corrected["risk_classification"] == "critical"
    assert report is not None
    assert report["applied"] is True
    assert any(item["field"] == "risk_classification" and item["status"] == "corrected" for item in report["fields"])


def test_semantic_output_correction_respects_threshold() -> None:
    corrected, report = correct_semantic_enum_fields(
        payload={"risk_classification": "critcal"},
        policy={
            "enabled": True,
            "similarity_threshold": 0.99,
            "min_margin": 0.0,
            "lexical_weight": 1.0,
            "embedding_provider": {"provider": "local", "dimensions": 12},
            "fields": {"risk_classification": {"enabled": True, "candidates": ["low", "medium", "high", "critical"]}},
        },
    )

    assert corrected["risk_classification"] == "critcal"
    assert report is not None
    assert report["applied"] is False


def test_semantic_output_correction_reports_provider_unavailable() -> None:
    corrected, report = correct_semantic_enum_fields(
        payload={"risk_classification": "critical-ish"},
        policy={
            "enabled": True,
            "similarity_threshold": 0.8,
            "embedding_provider": {"provider": "openai_compatible", "base_url": "", "api_key": ""},
            "fields": {"risk_classification": {"enabled": True, "candidates": ["low", "medium", "high", "critical"]}},
        },
    )

    assert corrected["risk_classification"] == "critical-ish"
    assert report is not None
    assert report["applied"] is False
    assert report["reason"] == "embedding_provider_unavailable"
