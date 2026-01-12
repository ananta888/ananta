try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
except ImportError:
    # Minimaler Mock falls nicht installiert
    class MockMetric:
        def inc(self, *args, **kwargs): pass
        def set(self, *args, **kwargs): pass
        def labels(self, *args, **kwargs): return self
        def observe(self, *args, **kwargs): pass
        def time(self): return self
        def __enter__(self): return self
        def __exit__(self, *args): pass
    Counter = Histogram = Gauge = lambda *a, **kw: MockMetric()
    generate_latest = lambda: b""
    CONTENT_TYPE_LATEST = "text/plain"

# Metriken
TASK_RECEIVED = Counter("task_received_total", "Total tasks received")
TASK_COMPLETED = Counter("task_completed_total", "Total tasks completed")
TASK_FAILED = Counter("task_failed_total", "Total tasks failed")
LLM_CALL_DURATION = Histogram("llm_call_duration_seconds", "Duration of LLM calls")
HTTP_REQUEST_DURATION = Histogram("http_request_duration_seconds", "HTTP request duration", ["method", "target"])
RETRIES_TOTAL = Counter("retries_total", "Total number of retries")
SHELL_POOL_SIZE = Gauge("shell_pool_size", "Total size of the shell pool")
SHELL_POOL_BUSY = Gauge("shell_pool_busy", "Number of busy shells in the pool")
SHELL_POOL_FREE = Gauge("shell_pool_free", "Number of free shells in the pool")

# Ressourcen Metriken
CPU_USAGE = Gauge("process_cpu_usage_percent", "CPU usage of the agent process")
RAM_USAGE = Gauge("process_ram_usage_bytes", "RAM usage of the agent process")
