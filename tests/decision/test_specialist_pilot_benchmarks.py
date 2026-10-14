"""Frozen split/calibration and cache-verification regressions, without models."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agent.services.decision_providers.benchmark_corpus import contract, corpus
from agent.services.decision_providers.calibration import CalibrationSample, fit_temperature
from agent.services.vision_decision_benchmark import image_corpus
from agent.services.vision_decision_provider import VisionDecisionSchema, parse_decision_response
from worker.decision.jeff_runtime import verify_weights

pytestmark = pytest.mark.timeout(20)


@pytest.mark.parametrize("task", ["tool_routing", "retrieval_relevance", "priority"])
def test_translation_pairs_and_holdout_are_group_isolated(task):
    manifest, partitions = corpus(task)
    groups = {name: {e.group_key for e in rows} for name, rows in partitions.items()}
    assert not groups["train"] & groups["validation"]
    assert not groups["validation"] & groups["holdout"]
    assert not groups["train"] & groups["holdout"]
    assert all(set(e.input["language"] for e in rows) == {"de", "en"} for rows in partitions.values())
    assert manifest.holdout.holdout_digest == corpus(task)[0].holdout.holdout_digest
    assert all(e.provenance.run_id is None for rows in partitions.values() for e in rows)


def test_calibration_does_not_accept_any_holdout_or_duplicate_identity():
    sample = CalibrationSample("case", "validation", (0.9, 0.1), 0)
    assert fit_temperature((sample,))["status"] == "insufficient_validation"
    with pytest.raises(ValueError, match="unique_validation"):
        fit_temperature((replace(sample, split="holdout"),))
    with pytest.raises(ValueError, match="unique_validation"):
        fit_temperature((sample, sample))


def test_vision_corpus_has_original_labels_and_frozen_groups():
    import hashlib
    cases = image_corpus()
    assert len(cases) == 120
    group_splits = {}
    for case in cases:
        group_splits.setdefault(case.group, set()).add(case.split)
        assert case.image.startswith(b"\x89PNG")
    assert all(len(splits) == 1 for splits in group_splits.values())
    assert any(c.variant == "injection" for c in cases if c.split == "holdout")
    digests = {
        split: {hashlib.sha256(c.image).hexdigest() for c in cases if c.split == split}
        for split in ("train", "validation", "holdout")
    }
    assert not digests["train"] & digests["validation"]
    assert not digests["train"] & digests["holdout"]
    assert not digests["validation"] & digests["holdout"]


def test_strict_vision_profile_rejects_old_or_unverified_cache_behavior():
    schema = VisionDecisionSchema.from_mapping("cache-test", {"flag": {"type": "boolean", "description": "flag?"}})
    body = json.dumps({"object": "decision", "model": "local", "results": []}).encode()
    result = parse_decision_response(
        200, body, schema=schema, n_contexts=1, min_probability=0.8, min_margin=0.3, require_cache_isolation=True
    )
    assert not result.ok and result.error_code == "cache_isolation_unverified"


def test_pinned_model_manifest_cannot_be_replaced_by_an_empty_inventory(tmp_path):
    with pytest.raises(ValueError, match="manifest_unverified"):
        verify_weights(tmp_path, {"model_revision": "d0a4e53d09cebe6bc963dd9be319d4279084bb2d", "files": {}})


def test_specialist_baseline_uses_existing_contract_and_never_claims_training():
    from agent.services.decision_providers.specialist_baseline import SpecialistDecisionBaseline
    from agent.services.decision_providers.simple import StaticDecisionProvider
    from agent.services.decision_providers.types import DecisionAnswer

    baseline = SpecialistDecisionBaseline(
        StaticDecisionProvider(
            lambda request: {"decision": DecisionAnswer("decision", "choice", choice="search", confidence=0.98)}
        ),
        contract("tool_routing"),
    )
    output = baseline.predict({"text": "find code", "language": "en"}, tenant_id="a", subject_id="b")
    assert output["label"] == "search"
    assert baseline.descriptor["training"] == "unsupported"
    assert baseline.descriptor["grants_permission"] is False
