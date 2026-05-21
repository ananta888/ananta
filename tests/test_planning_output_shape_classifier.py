from __future__ import annotations

from agent.services.planning_output_shape_classifier import classify_output_shape


def test_partial_json_is_detected():
    result = classify_output_shape('[{"title":"Task 1","description":"Implement')
    assert result["primary_shape"] == "partial_json"
    assert "partial_json" in result["detected_shapes"]


def test_markdown_fenced_json_wins_over_other_shapes():
    result = classify_output_shape("```json\n[{\"title\":\"Task 1\"}]\n```")
    assert result["primary_shape"] == "json_in_markdown_fence"
