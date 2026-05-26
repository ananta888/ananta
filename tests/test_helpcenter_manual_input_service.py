from __future__ import annotations

import pytest

from agent.services.helpcenter_manual_input_service import create_manual_helpcenter_message


def test_manual_input_creates_helpcenter_message() -> None:
    payload = create_manual_helpcenter_message(
        title="Build failed on CI",
        text="pytest failed in test_goal_api.py",
        severity="error",
        source_ref="manual://incident/1",
    )
    assert payload["source_kind"] == "manual_note"
    assert payload["title"] == "Build failed on CI"
    assert payload["severity"] == "error"


def test_manual_input_requires_title() -> None:
    with pytest.raises(ValueError):
        create_manual_helpcenter_message(title="", text="x")


def test_manual_input_requires_text() -> None:
    with pytest.raises(ValueError):
        create_manual_helpcenter_message(title="x", text="")
