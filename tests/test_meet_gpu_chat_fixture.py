"""CPU-only verification of GPU test prerequisites, explicitly not GPU evidence."""

from uuid import uuid4

from agent.services.meet_turn_service import HubMediaTasks
from tests.test_meet_chat_admission import session
from tests.test_meet_media import turn
from tests.test_meet_media_gpu import seed_gpu_chat_parent


def test_gpu_chat_fixture_creates_a_real_authoritative_parent_before_media_ingestion(app):
    from agent.database import engine
    from agent.services.repository_registry import get_repository_registry

    with app.app_context():
        scope = session().scope
        seed_gpu_chat_parent(engine, scope)
        parent = get_repository_registry().task_repo.get_by_id(scope.task_id)
        assert parent.status == "in_progress"
        assert (parent.tenant_id, parent.project_id) == (scope.tenant_id, scope.project_id)
        request = turn() | {"task_id": str(uuid4()), "binding_task_id": parent.id}
        tasks = HubMediaTasks()
        tasks.start(request, "synthetic-test-actor")
        tasks.require_current(request)
        assert tasks.finish(request, "completed")
