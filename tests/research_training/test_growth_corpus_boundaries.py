"""Boundary coverage for corpus text budgets and translation group separation."""

import pytest

from tests.research_training.test_growth_corpus import corpus
from worker.training.growth.corpus import freeze_articles


@pytest.mark.parametrize("maximum_chars", [True, False, 100.0, "100", None])
def test_rejects_invalid_text_budgets(maximum_chars):
    rows, entities = corpus()
    with pytest.raises(ValueError, match="growth_corpus_text_budget_invalid"):
        freeze_articles(rows, entities, maximum_chars=maximum_chars)


def test_minimum_text_budget_preserves_group_splits():
    maximum_chars = 100
    rows, entities = corpus()
    splits, manifest = freeze_articles(rows, entities, maximum_chars=maximum_chars)
    for languages in splits.values():
        for texts in languages.values():
            assert all(len(text) <= maximum_chars for text in texts)
    groups = {}
    for record in manifest["records"]:
        groups.setdefault(record["group"], set()).add(record["split"])
    assert all(len(values) == 1 for values in groups.values())
