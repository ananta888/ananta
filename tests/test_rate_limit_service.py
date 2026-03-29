from agent.services.rate_limit_service import RateLimitService


def test_rate_limit_service_limits_per_namespace_and_subject():
    service = RateLimitService()

    assert service.allow_request(namespace="sgpt", subject="user-a", limit=2, window_seconds=60) is True
    assert service.allow_request(namespace="sgpt", subject="user-a", limit=2, window_seconds=60) is True
    assert service.allow_request(namespace="sgpt", subject="user-a", limit=2, window_seconds=60) is False

    assert service.allow_request(namespace="sgpt", subject="user-b", limit=2, window_seconds=60) is True
    assert service.allow_request(namespace="system", subject="user-a", limit=2, window_seconds=60) is True


def test_rate_limit_service_clear_namespace_resets_in_memory_buckets():
    service = RateLimitService()

    assert service.allow_request(namespace="tasks", subject="127.0.0.1", limit=1, window_seconds=60) is True
    assert service.allow_request(namespace="tasks", subject="127.0.0.1", limit=1, window_seconds=60) is False

    service.clear_namespace("tasks")

    assert service.allow_request(namespace="tasks", subject="127.0.0.1", limit=1, window_seconds=60) is True
