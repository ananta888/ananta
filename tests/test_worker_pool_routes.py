from agent.db_models import WorkerSlotLeaseDB
from agent.repository import worker_slot_lease_repo


def test_worker_pool_routes_status_and_lists(client, admin_auth_header):
    worker_slot_lease_repo.save(
        WorkerSlotLeaseDB(
            lease_type="worker",
            status="queued",
            worker_id="w1",
            runtime_target_id="rt1",
            queue_position=1,
        )
    )

    r1 = client.get("/api/worker-pool/status", headers=admin_auth_header)
    assert r1.status_code == 200

    r2 = client.get("/api/worker-pool/leases", headers=admin_auth_header)
    assert r2.status_code == 200
    assert isinstance((r2.get_json().get("data") or {}).get("items"), list)

    r3 = client.get("/api/worker-pool/queues", headers=admin_auth_header)
    assert r3.status_code == 200


def test_worker_pool_cleanup_requires_admin(client):
    r = client.post("/api/worker-pool/cleanup-stale-leases")
    assert r.status_code in {401, 403}


def test_worker_pool_revalidate_endpoint(client, admin_auth_header):
    lease = worker_slot_lease_repo.save(
        WorkerSlotLeaseDB(
            lease_type="worker",
            status="queued",
            worker_id="w2",
            runtime_target_id="rt2",
            queue_position=1,
            lease_metadata={"policy_decision_hash": "h1", "policy_decision_ref": "r1"},
        )
    )
    resp = client.post(
        "/api/worker-pool/revalidate-queued",
        headers=admin_auth_header,
        json={"slot_lease_id": lease.id, "policy_decision_hash": "h1", "policy_decision_ref": "r1"},
    )
    assert resp.status_code == 200
    decision = (resp.get_json() or {}).get("data") or {}
    assert decision.get("status") == "active"
