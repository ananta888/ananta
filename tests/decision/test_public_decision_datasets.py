"""Public corpus identity, oracle separation and calibration never need network."""

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from agent.services import vision_decision_benchmark as vision
from agent.services.decision_providers.public_benchmark import run_public_intent, validate_corpus
from agent.services.decision_providers.simple import StaticDecisionProvider
from agent.services.decision_providers.types import DecisionAnswer, DecisionUsage
from agent.services.public_decision_datasets import DOCLAYNET_REVISION, INTENTS, freeze_massive, load_doclaynet
from tests.decision.test_vision_pilot_dataset import UnavailableProvider


def inputs():
    return {
        split: {
            language: [
                {
                    "id": f"{split}-{label}-{i}",
                    "label": label,
                    "text": f"{language} {split} original {label} utterance {i}",
                }
                for label in INTENTS
                for i in range(6)
            ]
            for language in ("de", "en")
        }
        for split in ("validation", "holdout")
    }


def test_freeze_is_order_independent_and_preserves_translation_groups():
    rows = inputs()
    corpus = freeze_massive(rows, per_label=5)
    for split in rows.values():
        for values in split.values():
            values.reverse()
    assert freeze_massive(rows, per_label=5) == corpus
    assert len(validate_corpus(corpus)) == 160
    groups = {}
    for row in corpus["records"]:
        groups.setdefault(row["group"], set()).add(row["split"])
    assert all(len(values) == 1 for values in groups.values())


@pytest.mark.parametrize("mutation", ["digest", "duplicate", "translation", "license", "revision"])
def test_mutated_or_unbound_corpus_never_reaches_inference(mutation):
    corpus = freeze_massive(inputs(), per_label=5)
    if mutation == "digest":
        corpus["records"][0]["text"] += " changed"
    elif mutation == "duplicate":
        corpus["records"].append(corpus["records"][0])
    elif mutation == "translation":
        corpus["records"].pop(0)
    else:
        corpus["declared_license" if mutation == "license" else "source_revision"] = "unbound"
    with pytest.raises(ValueError, match="public_intent_"):
        validate_corpus(corpus)


def test_holdout_labels_cannot_change_validation_temperature_or_threshold():
    corpus = freeze_massive(inputs(), per_label=5)
    label = next(iter(INTENTS))
    provider = StaticDecisionProvider(
        lambda request: {
            "intent": DecisionAnswer(
                "intent",
                "choice",
                choice=label,
                confidence=0.99,
                probabilities={key: 0.99 if key == label else 0.01 / 7 for key in INTENTS},
            )
        }
    )
    first = run_public_intent(provider, corpus)
    changed = copy.deepcopy(corpus)
    labels = tuple(INTENTS)
    for row in changed["records"]:
        if row["split"] == "holdout":
            row["label"] = labels[(labels.index(row["label"]) + 1) % len(labels)]
    second = run_public_intent(provider, changed)
    for language in ("de", "en"):
        assert first["reports"][language]["calibration"] == second["reports"][language]["calibration"]
        assert first["reports"][language]["threshold"] == second["reports"][language]["threshold"]
        assert "split" not in first["reports"][language]["raw_confidence"]["holdout"]
    assert not first["production_release_evidence"] and first["decision"] == "shadow"


def test_public_benchmark_preserves_usage_in_raw_and_calibrated_reports(monkeypatch):
    corpus = freeze_massive(inputs(), per_label=5)
    label = next(iter(INTENTS))
    provider = StaticDecisionProvider(
        lambda request: {
            "intent": DecisionAnswer(
                "intent",
                "choice",
                choice=label,
                confidence=0.99,
                probabilities={key: 0.99 if key == label else 0.01 / 7 for key in INTENTS},
            )
        }
    )
    decide = provider.decide

    def metered_decide(request, **kwargs):
        return replace(decide(request, **kwargs), usage=DecisionUsage(11, 3, 0.0125))

    monkeypatch.setattr(provider, "decide", metered_decide)
    report = run_public_intent(provider, corpus)
    assert len(report["raw"]) == 160
    for row in report["raw"]:
        assert (row["input_tokens"], row["output_tokens"], row["cost_usd"]) == (11, 3, 0.0125)
    for language in ("de", "en"):
        assert report["reports"][language]["calibration"]["status"] == "fitted_experimental"
        for variant in ("raw_confidence", "temperature_scaled"):
            for split in ("validation", "holdout"):
                metrics = report["reports"][language][variant][split]
                assert metrics["cases"] == 40
                assert metrics["tokens"] == {"input": 440, "output": 120}
                assert metrics["cost_usd"] == {"total": 0.5, "per_1000_decisions": 12.5}


def test_external_vision_dataset_uses_its_own_fields_and_rights():
    cases = tuple(replace(case, expected={"real_field": True}) for case in vision.image_corpus())
    dataset = vision.VisionPilotDataset(
        cases, vision.schema, "external-test", "LicenseRef-test", "external_dataset_technical_observation"
    )
    report = vision.run_vision_pilot(UnavailableProvider(), dataset=dataset)
    assert report["dataset_rights"] == "LicenseRef-test"
    assert set(report["reports"]) == {"de/real_field", "en/real_field"}
    assert report["classification"] == "external_dataset_technical_observation"
    assert report["evaluation_validity"] == "inference_failed"


def test_doclaynet_image_replacement_and_path_traversal_are_rejected(tmp_path):
    image = b"test-only bytes; image decoding is a separate provider responsibility"
    digest = hashlib.sha256(image).hexdigest()
    path = tmp_path / (digest + ".jpg")
    path.write_bytes(image)
    row = {
        "image_sha256": digest,
        "document": "original.pdf",
        "split": "train",
        "document_category": "manuals",
        "has_table": False,
        "has_picture": True,
    }
    manifest = {"source_revision": DOCLAYNET_REVISION, "declared_license": "CDLA-Permissive-1.0", "records": [row] * 3}
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(manifest))
    path.write_bytes(b"replaced")
    with pytest.raises(ValueError, match="binding_invalid"):
        load_doclaynet(tmp_path)
    row["image_sha256"] = "../untrusted"
    target.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="digest_invalid"):
        load_doclaynet(tmp_path)
