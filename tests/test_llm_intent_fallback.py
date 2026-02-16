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


def test_infer_tool_calls_role_links_scrum():
    calls = _infer_tool_calls_from_prompt("Bitte Rollen verknuepfen fuer Scrum")
    assert calls == [{"name": "ensure_team_templates", "args": {"team_types": ["Scrum"]}}]


def test_infer_tool_calls_create_team_with_name_and_type():
    calls = _infer_tool_calls_from_prompt('Bitte Team erstellen: Teamname: Phoenix Scrum')
    assert calls == [{"name": "create_team", "args": {"name": "Phoenix Scrum", "team_type": "Scrum"}}]


def test_infer_tool_calls_create_team_without_name_is_blocked():
    calls = _infer_tool_calls_from_prompt("Bitte neues Scrum Team anlegen")
    assert calls == []


def test_infer_tool_calls_assign_role_requires_explicit_ids():
    calls = _infer_tool_calls_from_prompt(
        "Bitte Rolle zuweisen team_id=t-123 role_id=r-po agent_url=http://localhost:5501"
    )
    assert calls == [
        {
            "name": "assign_role",
            "args": {"team_id": "t-123", "role_id": "r-po", "agent_url": "http://localhost:5501"},
        }
    ]


def test_infer_tool_calls_assign_role_without_required_fields_is_blocked():
    calls = _infer_tool_calls_from_prompt("Bitte Agent alpha als Product Owner zuweisen")
    assert calls == []
