"""Actual ordinary Hub tasks retain legacy wire, role policy and immutable binding."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_preauthorization import MeetDialogPreauthorization
from tests.meet_dialog_lifecycle_fixture import seed_parent
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_preauthorization_policy import document
from tests.test_meet_preauthorization_store import store  # noqa: F401

pytestmark = pytest.mark.timeout(45)
OPERATOR = "local-uid:1000"


def setup(store, *, provision=True):  # noqa: F811
    from agent.database import engine

    f = system()
    seed_parent(engine)
    f.store = store
    f.store.clock = lambda: f.f.now
    f.document = document() | {
        "parent_task_id": "meet-test-parent",
        "capabilities": f.payload["capabilities"],
        "valid_from": int(f.f.now) - 1,
        "expires_at": int(f.f.now) + 900,
    }
    if provision:
        f.store.provision(f.document, 0, OPERATOR)
    f.f.authority.preauthorization = MeetDialogPreauthorization(f.store)
    return f


def start(f):
    value = f.service.start(f.principal, "project", f.payload, parent="meet-test-parent")
    f.task_id = value["task_id"]
    f.context = f.tasks.get_by_id(f.task_id).worker_execution_context["meet_dialog"]
    f.ids = f.task_id, f.context["lease_id"], f.context["runtime_id"]
    return value


def test_authorized_start_controls_and_revocation_use_actual_hub_task_without_wire_changes(app, store):  # noqa: F811
    from ananta_contracts.meet_dialog import validate_assignment

    with app.app_context():
        f = setup(store)
        start(f)
        wire = f.worker.start_dialog.call_args.args[0]
        assert validate_assignment(wire, f.f.now) == wire
        assert "preauthorization" not in str(wire)
        task = f.tasks.get_by_id(f.task_id)
        binding = task.worker_execution_context["meet_preauthorization"]
        assert binding["revision"] == 1
        f.service.control(
            f.principal,
            "project",
            f.task_id,
            {
                "expected_revision": 1,
                "chat": False,
                "audio": False,
                "screen": False,
                "avatar": True,
            },
        )
        assert f.f.authority.current(*f.ids).controls.avatar.enabled
        assert f.tasks.get_by_id(f.task_id).worker_execution_context["meet_preauthorization"] == binding
        f.store.revoke(f.document["policy_id"], 1, OPERATOR)
        with pytest.raises(MeetError, match="preauthorization_binding_denied"):
            f.f.authority.current(*f.ids)
        with pytest.raises(MeetError):
            f.service.exchange(dict(zip(("task_id", "lease_id", "runtime_id"), f.ids)) | {"meet_session_id": "foreign"})
        assert f.service.inspect(f.principal, "project", f.task_id, stop=True)["status"] == "cancelled"
        f.worker.start_dialog.assert_called_once()


def test_missing_policy_denies_before_task_ingestion_or_worker_contact(app, store):  # noqa: F811
    with app.app_context():
        f = setup(store, provision=False)
        f.tasks.start = Mock()
        with pytest.raises(MeetError, match="preauthorization_missing"):
            start(f)
        f.tasks.start.assert_not_called()
        f.worker.start_dialog.assert_not_called()


def test_static_capability_ceiling_and_current_parent_authority_remain_mandatory(app, store):  # noqa: F811
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        f = setup(store)
        f.f.authority.policies[("tenant", "project")] = frozenset()
        with pytest.raises(MeetError, match="start_denied"):
            start(f)
        f.f.authority.policies[("tenant", "project")] = frozenset(f.payload["capabilities"])
        start(f)
        assert compare_and_set_local_task_status("meet-test-parent", "completed", expected_statuses={"in_progress"})
        with pytest.raises(MeetError, match="parent_inactive"):
            f.f.authority.current(*f.ids)


def test_disabling_provider_never_silently_restores_legacy_authority_for_bound_task(app, store):  # noqa: F811
    with app.app_context():
        f = setup(store)
        start(f)
        f.f.authority.preauthorization = None
        with pytest.raises(MeetError, match="provider_required"):
            f.f.authority.current(*f.ids)
        assert f.service.inspect(f.principal, "project", f.task_id, stop=True)["status"] == "cancelled"


@pytest.mark.parametrize("change", ["remove", "null", "revision", "digest"])
def test_generic_task_save_cannot_remove_or_rebind_active_preauthorization(app, store, change):  # noqa: F811
    from agent.services.repository_registry import get_repository_registry

    with app.app_context():
        f = setup(store)
        start(f)
        repository = get_repository_registry().task_repo
        task = repository.get_by_id(f.task_id)
        before = deepcopy(task.worker_execution_context)
        if change == "remove":
            task.worker_execution_context.pop("meet_preauthorization")
        elif change == "null":
            task.worker_execution_context["meet_preauthorization"] = None
        else:
            task.worker_execution_context["meet_preauthorization"][
                "revision" if change == "revision" else "assignment_digest"
            ] = 2 if change == "revision" else "0" * 64
        with pytest.raises(ValueError, match="preauthorization_immutable"):
            repository.save(task)
        assert repository.get_by_id(f.task_id).worker_execution_context == before


def test_enabling_stricter_mode_never_retrofits_existing_legacy_task(app, store):  # noqa: F811
    with app.app_context():
        f = setup(store)
        provider = f.f.authority.preauthorization
        f.f.authority.preauthorization = None
        start(f)
        f.f.authority.preauthorization = provider
        with pytest.raises(MeetError, match="binding_invalid"):
            f.f.authority.current(*f.ids)
        assert "meet_preauthorization" not in f.tasks.get_by_id(f.task_id).worker_execution_context


@pytest.mark.parametrize("failure", ["ingestion", "worker", "concurrent-revoke"])
def test_uncertain_start_burns_allowance_and_never_retries_dispatch(app, store, failure):  # noqa: F811
    with app.app_context():
        f = setup(store, provision=False)
        f.store.provision(f.document | {"max_dispatches": 1}, 0, OPERATOR)
        native_start = f.tasks.start
        if failure == "ingestion":
            f.tasks.start = Mock(side_effect=OSError("synthetic uncertain ingestion"))
        elif failure == "worker":
            f.worker.start_dialog.side_effect = OSError("synthetic uncertain dispatch")
        else:
            f.worker.start_dialog.side_effect = lambda _wire: f.store.revoke(f.document["policy_id"], 1, OPERATOR)
        with pytest.raises((MeetError, OSError)):
            start(f)
        if failure == "ingestion":
            f.worker.start_dialog.assert_not_called()
        else:
            f.worker.start_dialog.assert_called_once()
            wire = f.worker.start_dialog.call_args.args[0]
            assert f.tasks.get_by_id(wire["task_id"]).status == "failed"
        f.tasks.start = native_start
        before = f.worker.start_dialog.call_count
        with pytest.raises(MeetError, match="inactive_or_exhausted"):
            start(f)
        assert f.worker.start_dialog.call_count == before
