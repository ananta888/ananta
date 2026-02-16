from agent.routes.config import _infer_tool_calls_from_prompt


def test_infer_tool_calls_scrum_templates():
    calls = _infer_tool_calls_from_prompt("Bitte erstelle alle Templates fuer ein Scrum Team")
    assert calls == [{"name": "ensure_team_templates", "args": {"team_types": ["Scrum"]}}]


def test_infer_tool_calls_kanban_templates():
    calls = _infer_tool_calls_from_prompt("Lege bitte Vorlagen fuer Kanban an")
    assert calls == [{"name": "ensure_team_templates", "args": {"team_types": ["Kanban"]}}]


def test_infer_tool_calls_non_template_prompt():
    calls = _infer_tool_calls_from_prompt("Wie ist der aktuelle Status?")
    assert calls == []
