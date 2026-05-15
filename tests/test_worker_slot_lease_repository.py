import time

from agent.db_models import WorkerSlotLeaseDB
from agent.repository import worker_slot_lease_repo


def test_worker_slot_lease_repository_crud_and_release_idempotent():
    lease = worker_slot_lease_repo.save(
        WorkerSlotLeaseDB(
            lease_type="combined",
            status="active",
            worker_id="w1",
            runtime_target_id="rt1",
            ollama_endpoint="http://ollama:11434",
            ollama_model="ananta-default:latest",
            deadline_at=time.time() + 10,
        )
    )
    fetched = worker_slot_lease_repo.get_by_id(lease.id)
    assert fetched is not None
    assert fetched.worker_id == "w1"

    active = worker_slot_lease_repo.list_active()
    assert any(item.id == lease.id for item in active)

    first = worker_slot_lease_repo.release(lease.id)
    second = worker_slot_lease_repo.release(lease.id)
    assert first is not None
    assert second is not None
    assert second.status == "released"


def test_worker_slot_lease_repository_expired_query():
    lease = worker_slot_lease_repo.save(
        WorkerSlotLeaseDB(
            lease_type="worker",
            status="active",
            worker_id="w-expired",
            deadline_at=time.time() - 1,
        )
    )
    expired = worker_slot_lease_repo.list_expired()
    assert any(item.id == lease.id for item in expired)
