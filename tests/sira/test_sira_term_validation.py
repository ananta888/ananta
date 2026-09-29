from __future__ import annotations

from collections.abc import Mapping, Sequence

from worker.retrieval.sira.config import SiraConfig
from worker.retrieval.sira.contracts import CorpusBinding, CorpusTermStat, GeneratedTerm
from worker.retrieval.sira.term_validator import CorpusTermValidator


class FakeStatistics:
    def lookup(self, terms: Sequence[str], *, binding: CorpusBinding) -> Mapping[str, CorpusTermStat]:
        assert binding.scope == "repo-a"
        return {
            "retry": CorpusTermStat("retry", 2, 4, 10, ("original_text",)),
            "common": CorpusTermStat("common", 9, 90, 10, ("original_text",)),
            "abc123def456abc123def456": CorpusTermStat("abc123def456abc123def456", 1, 1, 10, ("enrichment_text",)),
        }


def _binding() -> CorpusBinding:
    return CorpusBinding(
        tenant_id="tenant-a",
        scope="repo-a",
        repository_revision="rev-1",
        source_manifest_hash="manifest-1",
        index_digest="index-1",
        statistics_digest="stats-1",
        profile_version="corpus-discriminative-lexical.v1",
    )


def test_term_validation_is_scope_bound_and_explainable():
    validator = CorpusTermValidator(statistics=FakeStatistics(), config=SiraConfig())
    decisions = validator.validate(
        [
            GeneratedTerm("retry", 0.9),
            GeneratedTerm("missing", 0.9),
            GeneratedTerm("common", 0.9),
            GeneratedTerm("abc123def456abc123def456", 0.9),
            GeneratedTerm("weak", 0.1),
        ],
        binding=_binding(),
    )
    assert [(item.term.value, item.accepted, item.reason_code) for item in decisions] == [
        ("retry", True, "accepted"),
        ("missing", False, "term_absent_from_scope"),
        ("common", False, "document_frequency_too_high"),
        ("abc123def456abc123def456", False, "rare_identifier_untrusted"),
        ("weak", False, "confidence_below_threshold"),
    ]


def test_original_terms_cannot_be_reintroduced_as_expansion():
    decision = CorpusTermValidator(statistics=FakeStatistics(), config=SiraConfig()).validate(
        [GeneratedTerm("retry", 0.9)],
        binding=_binding(),
        original_terms=["Retry"],
    )[0]
    assert decision.accepted is False
    assert decision.reason_code == "redundant_with_original"
