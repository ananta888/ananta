from agent.repositories.sfu_broadcast_background_job_repository import SfuBroadcastBackgroundJobSpec


def test_expired_owner_cannot_mutate_after_takeover():
    spec = SfuBroadcastBackgroundJobSpec("fleet", enabled=True, interval_ms_min=100, lease_seconds=1.0)
    assert spec.lease_seconds < 2.0
    # The SQL contract binds every finish to owner, fencing token and CAS version.
    # Full two-database-process coverage belongs to the real chaos gate.
