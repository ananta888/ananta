"""Frozen vision evaluation rejects leakage and incomplete execution headlessly."""

import json
from dataclasses import replace

import pytest

from agent.services import vision_decision_benchmark as benchmark
from agent.services.vision_benchmark_dataset import image_dataset_manifest
from agent.services.vision_decision_provider import VisionDecisionOutcome


class UnavailableProvider:
    def __init__(self):
        self.calls = []

    def decide(self, *args, **kwargs):
        self.calls.append(kwargs)
        return VisionDecisionOutcome.failure("unavailable")


def test_manifest_is_order_independent_and_covers_all_frozen_splits():
    cases = benchmark.image_corpus()
    manifest = image_dataset_manifest(cases)
    assert manifest == image_dataset_manifest(tuple(reversed(cases)))
    assert {split: row["cases"] for split, row in manifest["splits"].items()} == {
        "train": 40,
        "validation": 40,
        "holdout": 40,
    }
    assert manifest["disjoint_images"] and manifest["disjoint_groups"]
    changed = (*cases[:-1], replace(cases[-1], expected={"document_type": "changed"}))
    assert image_dataset_manifest(changed)["dataset_digest"] != manifest["dataset_digest"]


@pytest.mark.parametrize("contamination", ["image", "group", "identity", "language"])
def test_contaminated_or_incomplete_corpus_never_reaches_inference(monkeypatch, contamination):
    cases = list(benchmark.image_corpus())
    validation_index = next(index for index, case in enumerate(cases) if case.split == "validation")
    if contamination == "image":
        cases[validation_index] = replace(cases[validation_index], image=cases[0].image)
    elif contamination == "group":
        cases[validation_index] = replace(cases[validation_index], group=cases[0].group, variant="different")
    elif contamination == "identity":
        cases[1] = cases[0]
    else:
        cases = [case for case in cases if case.language != "en" or case.split != "holdout"]
    monkeypatch.setattr(benchmark, "image_corpus", lambda: tuple(cases))
    provider = UnavailableProvider()
    with pytest.raises(ValueError, match="vision_pilot_corpus_"):
        benchmark.run_vision_pilot(provider)
    assert not provider.calls


def test_failed_inference_is_not_a_complete_evaluation():
    provider = UnavailableProvider()
    report = benchmark.run_vision_pilot(provider)
    assert len(provider.calls) == 80
    assert report["evaluation_validity"] == "inference_failed"
    assert report["production_release_evidence"] is False
    assert all(row["holdout"]["errors"] == 20 for row in report["reports"].values())


def test_shared_deadline_rejects_late_result_without_dispatching_another_case():
    times = iter((0, 0, 2))
    provider = UnavailableProvider()
    with pytest.raises(ValueError, match="vision_pilot_deadline_exceeded"):
        benchmark.run_vision_pilot(provider, max_seconds=1, clock=lambda: next(times))
    assert len(provider.calls) == 1
    assert provider.calls[0]["deadline_monotonic"] == 1


@pytest.mark.parametrize("seconds", [0, -1, float("nan"), float("inf"), True, 3601])
def test_invalid_deadline_never_dispatches(seconds):
    provider = UnavailableProvider()
    with pytest.raises(ValueError, match="vision_pilot_deadline_invalid"):
        benchmark.run_vision_pilot(provider, max_seconds=seconds)
    assert not provider.calls


def test_truncated_holdout_is_not_published_as_full_evaluation():
    provider = UnavailableProvider()
    with pytest.raises(ValueError, match="vision_pilot_budget_insufficient"):
        benchmark.run_vision_pilot(provider, max_cases=79)
    assert not provider.calls


def test_cli_returns_failure_for_an_incomplete_evaluation(tmp_path, monkeypatch):
    from scripts import vision_decision_benchmark as cli

    report = {"cases": 80, "evaluation_validity": "inference_failed", "production_release_evidence": False}
    monkeypatch.setattr(cli, "run_vision_pilot", lambda *args, **kwargs: report)
    output = tmp_path / "failed.json"
    monkeypatch.setattr(
        "sys.argv", ["vision-pilot", "--url", "http://127.0.0.1:9", "--model", "synthetic", "--output", str(output)]
    )
    assert cli.main() == 2
    assert json.loads(output.read_text())["evaluation_validity"] == "inference_failed"
