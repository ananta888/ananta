from agent.services.cognitive_style_overlay_service import (
    CognitiveStyleOverlayService,
)
from agent.services.cognitive_style_service import (
    CognitiveStyleService,
    InMemoryCognitiveStyleStateRepository,
)


def test_role_overlay_changes_instruction_style_but_never_permissions():
    overlay = CognitiveStyleOverlayService(
        CognitiveStyleService(InMemoryCognitiveStyleStateRepository())
    ).apply(
        task={"task_kind": "review"},
        system_prompt="Prüfe den Patch.",
    )

    assert overlay.applied is True
    assert overlay.role_id == "reviewer"
    assert "Gegenhypothese" in str(overlay.rendered_system_prompt)
    assert "verändert keine Rechte" in str(overlay.rendered_system_prompt)
    assert overlay.permission_delta == "none"


def test_implementation_alias_uses_checklist_overlay_without_hidden_side_effect():
    overlay = CognitiveStyleOverlayService(
        CognitiveStyleService(InMemoryCognitiveStyleStateRepository())
    ).apply(
        task={"role_id": "coder"}, system_prompt=None,
    )

    assert overlay.role_id == "implementer"
    assert "Definition-of-Done" in str(overlay.rendered_system_prompt)
    assert overlay.permission_delta == "none"
