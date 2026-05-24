from __future__ import annotations

from agent.services.browser_intent_mapper import map_intent_to_browser_action


def test_intent_mapping_known_action():
    m = map_intent_to_browser_action("click")
    assert m.needs_review is False
    assert m.action == "click"


def test_intent_mapping_unknown_needs_review():
    m = map_intent_to_browser_action("compile")
    assert m.needs_review is True
    assert m.reason == "browser_intent_ambiguous_needs_review"
