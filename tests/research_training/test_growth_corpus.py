"""Deterministic multilingual source grouping without network access."""

import copy

import pytest

from worker.training.growth.corpus import freeze_articles


def corpus():
    rows, entities = {}, {}
    for language in ("de", "en"):
        rows[language] = [
            {
                "id": str(i),
                "text": f"{language} article {i} " * 12,
                "url": f"https://{language}.wikipedia.org/?curid={i}",
                "title": str(i),
            }
            for i in range(1, 151)
        ]
        entities[language] = {str(i): f"Q{i}" for i in range(1, 151)}
    return rows, entities


def test_translations_share_split_and_input_order_does_not_change_freeze():
    rows, entities = corpus()
    splits, manifest = freeze_articles(rows, entities)
    groups = {}
    for record in manifest["records"]:
        groups.setdefault(record["group"], set()).add(record["split"])
    assert all(len(values) == 1 for values in groups.values())
    for values in rows.values():
        values.reverse()
    assert freeze_articles(rows, entities) == (splits, manifest)
    assert not manifest["production_release_evidence"]


def test_missing_entity_and_duplicate_content_are_excluded():
    rows, entities = corpus()
    del entities["de"]["1"]
    rows["en"][1]["text"] = rows["de"][1]["text"]
    _, manifest = freeze_articles(rows, entities)
    records = manifest["records"]
    assert not any(r["language"] == "de" and r["article_id"] == "1" for r in records)
    assert len({r["content_sha256"] for r in records}) == len(records)


@pytest.mark.parametrize("change", ["language", "budget", "too_few"])
def test_invalid_corpus_fails_closed(change):
    rows, entities = copy.deepcopy(corpus())
    kwargs = {}
    if change == "language":
        del rows["en"]
    elif change == "budget":
        kwargs["maximum_chars"] = 4001
    else:
        rows["de"] = rows["de"][:3]
    with pytest.raises(ValueError):
        freeze_articles(rows, entities, **kwargs)


def test_cli_checks_output_before_training_and_preserves_existing_report(tmp_path, monkeypatch):
    import importlib.util
    import sys

    from worker.training.growth import experiment

    spec = importlib.util.spec_from_file_location("growth_cli_under_test", "scripts/adaptive_growth_experiment.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    calls = []
    monkeypatch.setattr(
        experiment,
        "run_experiment",
        lambda **kwargs: calls.append(kwargs) or {"rows": [], "decision": "experimental_only"},
    )
    monkeypatch.setattr(sys, "argv", ["growth", "--output", str(tmp_path / "missing" / "report.json")])
    assert cli.main() == 1
    assert not calls
    output = tmp_path / "report.json"
    output.write_text("previous")
    monkeypatch.setattr(sys, "argv", ["growth", "--output", str(output)])

    def failed(**kwargs):
        raise RuntimeError("training failed")

    monkeypatch.setattr(experiment, "run_experiment", failed)
    assert cli.main() == 1
    assert output.read_text() == "previous"
    assert not list(tmp_path.glob(".growth-observation-*"))
