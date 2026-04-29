from __future__ import annotations

import hashlib
import json

from worker.coding.native_patch_proposer import propose_patch_with_model
from worker.core.model_provider import DeterministicMockModelProvider


def test_native_patch_proposer_returns_patch_artifact() -> None:
    patch = "diff --git a/a.txt b/a.txt\n"
    provider = DeterministicMockModelProvider(
        responses=[json.dumps({"patch": patch, "changed_files": ["a.txt"], "risk_classification": "high"})]
    )
    result = propose_patch_with_model(
        model_provider=provider,
        prompt="propose",
        task_id="T1",
        capability_id="worker.patch.propose",
    )
    assert result["status"] == "ok"
    assert result["mode"] == "patch_artifact"
    assert result["artifact"]["patch_hash"] == hashlib.sha256(patch.encode("utf-8")).hexdigest()


def test_native_patch_proposer_returns_edit_plan_when_patch_missing() -> None:
    provider = DeterministicMockModelProvider(responses=[json.dumps({"edit_plan": ["update tests", "adjust parser"]})])
    result = propose_patch_with_model(
        model_provider=provider,
        prompt="propose",
        task_id="T1",
        capability_id="worker.patch.propose",
    )
    assert result["status"] == "ok"
    assert result["mode"] == "edit_plan"
    assert result["artifact"]["steps"] == ["update tests", "adjust parser"]


def test_native_patch_proposer_degrades_on_invalid_output() -> None:
    provider = DeterministicMockModelProvider(responses=["not-json"])
    result = propose_patch_with_model(
        model_provider=provider,
        prompt="propose",
        task_id="T1",
        capability_id="worker.patch.propose",
    )
    assert result["status"] == "degraded"
    assert result["reason"] == "invalid_model_output"


def test_native_patch_proposer_degrades_without_provider() -> None:
    result = propose_patch_with_model(
        model_provider=None,
        prompt="propose",
        task_id="T1",
        capability_id="worker.patch.propose",
    )
    assert result["status"] == "degraded"
    assert result["llm_used"] is False


def test_native_patch_proposer_semantically_corrects_risk_classification() -> None:
    patch = "diff --git a/a.txt b/a.txt\n"
    provider = DeterministicMockModelProvider(
        responses=[json.dumps({"patch": patch, "changed_files": ["a.txt"], "risk_classification": "critcal"})]
    )
    result = propose_patch_with_model(
        model_provider=provider,
        prompt="propose",
        task_id="T1",
        capability_id="worker.patch.propose",
        semantic_correction_policy={
            "enabled": True,
            "similarity_threshold": 0.8,
            "min_margin": 0.0,
            "lexical_weight": 1.0,
            "embedding_provider": {"provider": "local", "dimensions": 12},
            "fields": {"risk_classification": {"enabled": True, "candidates": ["low", "medium", "high", "critical"]}},
        },
    )

    assert result["status"] == "ok"
    assert result["artifact"]["risk_classification"] == "critical"
    assert (result.get("semantic_correction") or {}).get("applied") is True


def test_native_patch_proposer_falls_back_to_high_for_invalid_risk_classification() -> None:
    patch = "diff --git a/a.txt b/a.txt\n"
    provider = DeterministicMockModelProvider(
        responses=[json.dumps({"patch": patch, "changed_files": ["a.txt"], "risk_classification": "unknown-risk"})]
    )
    result = propose_patch_with_model(
        model_provider=provider,
        prompt="propose",
        task_id="T1",
        capability_id="worker.patch.propose",
    )

    assert result["status"] == "ok"
    assert result["artifact"]["risk_classification"] == "high"
