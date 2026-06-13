from __future__ import annotations

from voice_runtime.glossary import Glossary
from voice_runtime.postprocessing.rules import RuleBasedPostprocessor
from voice_runtime.preprocessing.vad import MockVadProcessor


def test_rule_based_postprocessor_applies_glossary_terms():
    glossary = Glossary(replacements={"code compass": "CodeCompass"})
    processor = RuleBasedPostprocessor(glossary=glossary)

    result = processor.process("open code compass")

    assert result.text == "Open CodeCompass."
    assert result.changed is True


def test_mock_vad_returns_single_passthrough_segment():
    processor = MockVadProcessor()

    segments = processor.split(filename="sample.webm", content=b"audio")

    assert len(segments) == 1
    assert segments[0].content == b"audio"
    assert segments[0].filename == "sample.webm"
