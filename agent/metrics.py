try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
except ImportError:
    # Minimaler Mock falls nicht installiert
    class MockMetric:
        def inc(self, *args, **kwargs): pass
        def labels(self, *args, **kwargs): return self
        def observe(self, *args, **kwargs): pass
        def time(self): return self
        def __enter__(self): return self
        def __exit__(self, *args): pass
    Counter = Histogram = lambda *a, **kw: MockMetric()
    generate_latest = lambda: b""
    CONTENT_TYPE_LATEST = "text/plain"

# Metriken
TASK_RECEIVED = Counter("task_received_total", "Total tasks received")
TASK_COMPLETED = Counter("task_completed_total", "Total tasks completed")
TASK_FAILED = Counter("task_failed_total", "Total tasks failed")
LLM_CALL_DURATION = Histogram("llm_call_duration_seconds", "Duration of LLM calls")
HTTP_REQUEST_DURATION = Histogram("http_request_duration_seconds", "HTTP request duration", ["method", "target"])
RETRIES_TOTAL = Counter("retries_total", "Total number of retries")
