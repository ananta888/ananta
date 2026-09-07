"""Real SQL child scopes with deterministic, explicitly synthetic media policy."""

from uuid import uuid4

import pytest

from agent.services.meet_dialog_lifecycle import organization_tuple
from agent.services.meet_turn_service import HubMediaTasks
from tests.meet_dialog_lifecycle_fixture import seed_parent
from tests.test_meet_dialog_audio import runtime
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_media import turn

pytestmark = pytest.mark.timeout(45)


def organized_dialog(engine):
    seed_parent(engine)
    f = system()
    payload = {
        "capabilities": ["audio.receive", "chat.send"],
        "duration_seconds": 300,
        "chat_mode": "off",
        "audio_mode": "transcribe",
    }
    started = f.service.start(f.principal, "project", payload, parent="meet-test-parent")
    dialog = f.tasks.get_by_id(started["task_id"])
    context = dialog.worker_execution_context["meet_dialog"]
    scope = f.f.authority.current(dialog.id, context["lease_id"], context["runtime_id"])
    _, receipt, _, _ = runtime()
    f.meet.inspect.return_value = receipt
    audio = {
        "task_id": dialog.id,
        "lease_id": scope.lease_id,
        "runtime_id": scope.runtime_id,
        "meet_session_id": receipt["lease"]["sessionId"],
        "publication_id": "audio",
        "nonce": "a" * 32,
    }
    return f, dialog, scope, audio


@pytest.mark.parametrize("kind", ["audio", "media"])
def test_audio_and_generated_media_inherit_the_entire_authorized_dialog_scope(app, kind):
    from agent.database import engine

    with app.app_context():
        f, dialog, _, audio = organized_dialog(engine)
        if kind == "audio":
            job = f.service.audio_coordinator.start(audio)["job"]
            child = f.tasks.get_by_id(job["task_id"])
        else:
            request = turn() | {"task_id": str(uuid4()), "binding_task_id": dialog.id}
            HubMediaTasks().start(request, "owner")
            child = f.tasks.get_by_id(request["task_id"])
        assert child.parent_task_id == dialog.id
        assert organization_tuple(child) == organization_tuple(dialog)
