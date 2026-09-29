from __future__ import annotations

from voice_runtime.glossary import Glossary
from voice_runtime.postprocessing.rules import LLMPostprocessor, RuleBasedPostprocessor, apply_edit_operations
from voice_runtime.preprocessing.vad import MockVadProcessor


def test_rule_based_postprocessor_applies_glossary_terms():
    glossary = Glossary(replacements={"code compass": "CodeCompass"})
    processor = RuleBasedPostprocessor(glossary=glossary)

    result = processor.process("open code compass")

    assert result.text == "Open CodeCompass."
    assert result.changed is True
    assert result.original_text == "open code compass"
    assert result.proposed_text == result.text
    assert result.review_required is False
    assert apply_edit_operations(result.original_text, result.edits) == result.text
    assert {edit.reason for edit in result.edits} == {
        "sentence_case_and_terminal_punctuation",
        "glossary_term",
    }


def test_postprocessor_normalizes_units_without_replacing_partial_glossary_words():
    glossary = Glossary(replacements={"ana": "Ananta"})
    result = RuleBasedPostprocessor(glossary=glossary).process("temperatur 20kg, ananas bleibt")

    assert result.text == "Temperatur 20 kg, ananas bleibt."
    assert "Anantanas" not in result.text
    assert result.reconstructed_text() == result.text


def test_large_postprocessing_change_becomes_review_proposal_not_silent_edit():
    processor = LLMPostprocessor(glossary=Glossary(replacements={"x": "sehr langer ersatz"}), max_change_ratio=0.1)

    result = processor.process("x")

    assert result.text == "x"
    assert result.proposed_text == "Sehr langer ersatz."
    assert result.changed is False
    assert result.review_required is True
    assert result.conflict_reason == "edit_distance_limit"
    assert apply_edit_operations(result.original_text, result.edits) == result.proposed_text


def test_mock_vad_returns_single_passthrough_segment():
    processor = MockVadProcessor()

    segments = processor.split(filename="sample.webm", content=b"audio")

    assert len(segments) == 1
    assert segments[0].content == b"audio"
    assert segments[0].filename == "sample.webm"
