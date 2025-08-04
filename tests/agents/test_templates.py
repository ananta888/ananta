from src.agents.templates import PromptTemplates


def test_render_and_add():
    mgr = PromptTemplates()
    mgr.add("hello", "Hi {name}")
    assert mgr.render("hello", name="Bob") == "Hi Bob"
    # Unknown templates yield empty string
    assert mgr.render("missing", name="Bob") == ""
