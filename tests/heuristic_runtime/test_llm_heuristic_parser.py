"""Tests für LLM Heuristic Parser (T06.02 + T06.03)."""
from __future__ import annotations

import json
import pytest

from agent.services.heuristic_runtime.llm_heuristic_parser import LlmHeuristicParser


def _valid_dsl_json():
    return {
        "dsl_version": "2.0",
        "observe": {"sources": ["tui.snapshot"]},
        "action": {"kind": "follow_artifact", "confidence": 0.8},
        "safety": {"safety_class": "ui_motion_only"},
        "provenance": {"created_by": "lab", "rationale": "test"},
    }


class TestLlmHeuristicParser:
    def setup_method(self):
        self.parser = LlmHeuristicParser()

    def test_valid_json_returns_dict(self):
        raw = json.dumps(_valid_dsl_json())
        result = self.parser.parse(raw)
        assert result is not None
        assert result["dsl_version"] == "2.0"

    def test_fenced_json_is_repaired(self):
        dsl = _valid_dsl_json()
        raw = f"```json\n{json.dumps(dsl)}\n```"
        result = self.parser.parse(raw)
        assert result is not None
        assert result.get("_repaired") is True

    def test_fenced_json_no_lang_is_repaired(self):
        dsl = _valid_dsl_json()
        raw = f"```\n{json.dumps(dsl)}\n```"
        result = self.parser.parse(raw)
        assert result is not None

    def test_json_embedded_in_text_no_trailing_text(self):
        # JSON at start (with prefix text) but no trailing text after closing brace
        dsl = _valid_dsl_json()
        raw = f"Here is the heuristic: {json.dumps(dsl)}"
        result = self.parser.parse(raw)
        # The parser tries to parse from the first '{' — succeeds if no trailing garbage
        assert result is not None

    def test_broken_json_returns_none(self):
        raw = '{"dsl_version": "2.0", "action": broken json'
        result = self.parser.parse(raw)
        assert result is None

    def test_empty_string_returns_none(self):
        result = self.parser.parse("")
        assert result is None

    def test_none_like_empty_returns_none(self):
        result = self.parser.parse("   ")
        assert result is None

    def test_missing_dsl_version_returns_none(self):
        dsl = _valid_dsl_json()
        del dsl["dsl_version"]
        result = self.parser.parse(json.dumps(dsl))
        assert result is None

    def test_wrong_dsl_version_returns_none(self):
        dsl = _valid_dsl_json()
        dsl["dsl_version"] = "1.0"
        result = self.parser.parse(json.dumps(dsl))
        assert result is None

    def test_missing_action_returns_none(self):
        dsl = _valid_dsl_json()
        del dsl["action"]
        result = self.parser.parse(json.dumps(dsl))
        assert result is None

    def test_missing_safety_returns_none(self):
        dsl = _valid_dsl_json()
        del dsl["safety"]
        result = self.parser.parse(json.dumps(dsl))
        assert result is None

    def test_missing_provenance_returns_none(self):
        dsl = _valid_dsl_json()
        del dsl["provenance"]
        result = self.parser.parse(json.dumps(dsl))
        assert result is None

    def test_hallucination_no_dsl_version_returns_none(self):
        # Hallucinated response with no required fields
        raw = '{"some_field": "value", "another": 123}'
        result = self.parser.parse(raw)
        assert result is None

    def test_plain_prose_returns_none(self):
        raw = "I think you should move the snake to the right and follow the artifact."
        result = self.parser.parse(raw)
        assert result is None

    def test_array_response_returns_none(self):
        # LLM sometimes returns arrays
        raw = json.dumps([{"dsl_version": "2.0", "action": {}, "safety": {}, "provenance": {}}])
        result = self.parser.parse(raw)
        assert result is None

    def test_extra_fields_are_preserved(self):
        dsl = _valid_dsl_json()
        dsl["experiment"] = {"max_ttl_seconds": 10}
        result = self.parser.parse(json.dumps(dsl))
        assert result is not None
        assert "experiment" in result
