import time
import pytest
import os

# Only run performance budget tests if explicitly enabled or in CI
# to avoid flaky failures during local development.
RUN_PERF_TESTS = os.environ.get("RUN_PERF_TESTS", "true").lower() == "true"

@pytest.mark.skipif(not RUN_PERF_TESTS, reason="Set RUN_PERF_TESTS=1 to run performance budget tests")
def test_startup_time_budget():
    """
    Ensures that the application starts within a reasonable time.
    Fulfills PRF-062.
    """
    from agent.ai_agent import create_app

    # We use a relatively high budget to account for varying CI environments
    # Locally it should be much faster (e.g. 5-8s)
    BUDGET_SECONDS = 20.0

    start = time.perf_counter()
    # Note: create_app initializes DB and background services
    _app = create_app(agent="perf-test")
    elapsed = time.perf_counter() - start

    print(f"Startup took {elapsed:.2f}s")
    assert elapsed < BUDGET_SECONDS, f"Startup took {elapsed:.2f}s, which is over the budget of {BUDGET_SECONDS}s"

@pytest.mark.skipif(not RUN_PERF_TESTS, reason="Set RUN_PERF_TESTS=1 to run performance budget tests")
def test_read_model_performance_budget(client, auth_header):
    """
    Ensures that the assistant read-model (large aggregate) is built quickly.
    """
    BUDGET_MS = 2000.0 # 2 seconds

    start = time.perf_counter()
    resp = client.get("/assistant/read-model", headers=auth_header)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert resp.status_code == 200
    print(f"Read-model took {elapsed_ms:.2f}ms")
    assert elapsed_ms < BUDGET_MS, f"Read-model took {elapsed_ms:.2f}ms, which is over the budget of {BUDGET_MS}ms"

@pytest.mark.skipif(not RUN_PERF_TESTS, reason="Set RUN_PERF_TESTS=1 to run performance budget tests")
def test_agents_list_performance_budget(client, auth_header):
    """
    Ensures that the agents list endpoint is fast.
    """
    BUDGET_MS = 500.0

    start = time.perf_counter()
    resp = client.get("/agents", headers=auth_header)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert resp.status_code == 200
    print(f"Agents list took {elapsed_ms:.2f}ms")
    assert elapsed_ms < BUDGET_MS, f"Agents list took {elapsed_ms:.2f}ms, which is over the budget of {BUDGET_MS}ms"
