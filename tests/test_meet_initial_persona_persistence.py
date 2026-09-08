"""Original persona pins survive actual Hub preauthorization/phase persistence."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.models.meet_preauthorization_binding import assignment_projection
from agent.repositories.meet_dialog_phases import TaskDialogPhases
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_phases import MeetDialogPhases
from tests.test_meet_dialog_avatar_selection import PIN
from tests.test_meet_preauthorization_hub import setup
from tests.test_meet_preauthorization_store import store as policy_store  # noqa: F401

pytestmark = pytest.mark.timeout(45)


def start(policy):
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    f = setup(policy)
    f.phases = TaskDialogPhases(f.tasks, task_status_cas=compare_and_set_local_task_status)
    f.service.phases = MeetDialogPhases(f.phases, f.f.authority, f.meet, clock=lambda: f.f.now)
    result = f.service.start(
        f.principal,
        "project",
        f.payload
        | {
            "avatar_images": True,
            "initial_persona": {"avatar": {"mode": "persona-image-v1", "profile": deepcopy(PIN)}},
        },
        parent="meet-test-parent",
    )
    f.task = f.tasks.get_by_id(result["task_id"])
    context = f.task.worker_execution_context["meet_dialog"]
    f.ids = f.task.id, context["lease_id"], context["runtime_id"]
    return f


def test_original_pin_survives_live_selection_and_restarted_phase_reader(app, request):
    from agent.services.repository_registry import get_repository_registry

    with app.app_context():
        f = start(request.getfixturevalue("policy_store"))
        original = deepcopy(f.task.worker_execution_context)
        assert f.service.phases.inspect(f.principal, "project", f.task.id)["phase"] == "connecting"
        f.service.select_avatar(f.principal, "project", f.task.id, {"expected_revision": 1, "neutral": True})
        current = f.f.authority.current(*f.ids)
        assert current.avatar_selection == {"mode": "neutral-ai-v1"}
        assert current.initial_persona == original["meet_dialog"]["initial_persona"]
        f.service.phases = MeetDialogPhases(f.phases, f.f.authority, f.meet, clock=lambda: f.f.now)
        assert f.service.phases.inspect(f.principal, "project", f.task.id)["phase"] == "connecting"
        changed = f.tasks.get_by_id(f.task.id)
        assert changed.worker_execution_context["meet_preauthorization"] == original["meet_preauthorization"]
        changed.worker_execution_context["meet_dialog"]["initial_persona"]["avatar"]["selection_digest"] = "b" * 64
        with pytest.raises(ValueError, match="initial_persona_immutable"):
            get_repository_registry().task_repo.save(changed)
        assert f.f.authority.current(*f.ids) == current
        assert f.service.inspect(f.principal, "project", f.task.id, stop=True)["status"] == "cancelled"
        assert f.service.phases.inspect(f.principal, "project", f.task.id)["phase"] == "cancelled"
        f.worker.start_dialog.assert_called_once()


@pytest.mark.parametrize("value", [None, {}, {"schema": "unknown", "avatar": {"private": "no-public-error"}}])
def test_malformed_stored_projection_is_a_bounded_domain_denial(app, request, value):
    with app.app_context():
        f = start(request.getfixturevalue("policy_store"))
        f.task.worker_execution_context["meet_dialog"]["initial_persona"] = value
        f.f.authority.tasks = Mock(get_by_id=Mock(return_value=f.task))
        with pytest.raises(MeetError, match="^meet_initial_persona_invalid$") as error:
            f.f.authority.current(*f.ids)
        assert error.value.status == 403
        with pytest.raises(MeetError, match="^meet_preauthorization_binding_invalid$"):
            assignment_projection(
                f.task.id, "tenant", "project", f.document["origin"], f.task.worker_execution_context["meet_dialog"]
            )
