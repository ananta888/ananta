import os
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

try:
    import requests
    HAVE_REQUESTS = True
except Exception:  # pragma: no cover
    HAVE_REQUESTS = False

BASE_URL = os.getenv("CONTROLLER_BASE_URL", "http://localhost:8081")


def _get(url: str, timeout: float = 2.0):
    start = time.perf_counter()
    resp = requests.get(url, timeout=timeout)
    elapsed = (time.perf_counter() - start) * 1000.0
    return resp, elapsed


@pytest.mark.smoke
@pytest.mark.skipif(not HAVE_REQUESTS, reason="requests not installed")
def test_smoke_config_latency():
    if os.getenv("RUN_SMOKE_TESTS") != "1":
        pytest.skip("Set RUN_SMOKE_TESTS=1 to enable smoke test")
    samples = []
    for _ in range(10):
        resp, ms = _get(f"{BASE_URL}/config", timeout=2.0)
        assert resp.ok
        samples.append(ms)
    # p95 below threshold (ms)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1]
    assert p95 < float(os.getenv("SMOKE_P95_MS", "300"))


@pytest.mark.load
@pytest.mark.skipif(not HAVE_REQUESTS, reason="requests not installed")
def test_load_config_concurrency():
    if os.getenv("RUN_LOAD_TESTS") != "1":
        pytest.skip("Set RUN_LOAD_TESTS=1 to enable load test")
    runs = int(os.getenv("LOAD_RUNS", "100"))
    conc = int(os.getenv("LOAD_CONCURRENCY", "20"))
    times = []
    errors = 0
    with ThreadPoolExecutor(max_workers=conc) as ex:
        futs = [ex.submit(_get, f"{BASE_URL}/config", 2.0) for _ in range(runs)]
        for fut in as_completed(futs):
            try:
                resp, ms = fut.result()
                if not resp.ok:
                    errors += 1
                times.append(ms)
            except Exception:
                errors += 1
    times.sort()
    p95 = times[int(len(times) * 0.95) - 1] if times else float("inf")
    assert errors == 0, f"errors={errors}"
    assert p95 < float(os.getenv("LOAD_P95_MS", "500"))
