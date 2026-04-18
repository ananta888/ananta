try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
except ImportError:
    # Minimaler Mock falls nicht installiert
    class MockMetric:
        def inc(self, *args, **kwargs):
            pass

        def set(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def observe(self, *args, **kwargs):
            pass

        def time(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def _mock_metric_factory(*args, **kwargs):
        return MockMetric()

    def generate_latest() -> bytes:
        return b""

    Counter = Histogram = Gauge = _mock_metric_factory
    CONTENT_TYPE_LATEST = "text/plain"

# Metriken
TASK_RECEIVED = Counter("task_received_total", "Total tasks received")
TASK_COMPLETED = Counter("task_completed_total", "Total tasks completed")
TASK_FAILED = Counter("task_failed_total", "Total tasks failed")
LLM_CALL_DURATION = Histogram("llm_call_duration_seconds", "Duration of LLM calls")
HTTP_REQUEST_DURATION = Histogram("http_request_duration_seconds", "HTTP request duration", ["method", "target"])
RETRIES_TOTAL = Counter("retries_total", "Total number of retries")
EVOLUTION_ANALYSES_TOTAL = Counter(
    "evolution_analyses_total",
    "Total Evolution analyses grouped by provider, trigger and outcome status",
    ["provider", "trigger_type", "status"],
)
EVOLUTION_PROPOSALS_TOTAL = Counter(
    "evolution_proposals_total",
    "Total Evolution proposals grouped by provider, proposal type, risk and review requirement",
    ["provider", "proposal_type", "risk_level", "requires_review"],
)
EVOLUTION_VALIDATIONS_TOTAL = Counter(
    "evolution_validations_total",
    "Total Evolution proposal validations grouped by provider and result status",
    ["provider", "status", "valid"],
)
EVOLUTION_APPLIES_TOTAL = Counter(
    "evolution_applies_total",
    "Total Evolution apply attempts grouped by provider and result status",
    ["provider", "status", "applied"],
)
EVOLUTION_OPERATION_DURATION_SECONDS = Histogram(
    "evolution_operation_duration_seconds",
    "Duration of Evolution provider operations",
    ["provider", "operation", "status"],
)
EVOLUTION_PROVIDER_FAILURES_TOTAL = Counter(
    "evolution_provider_failures_total",
    "Total Evolution provider failures grouped by provider, operation and error code",
    ["provider", "operation", "error_code", "transient"],
)
EVOLUTION_PROVIDER_RETRIES_TOTAL = Counter(
    "evolution_provider_retries_total",
    "Total Evolution provider retries grouped by provider, operation and error code",
    ["provider", "operation", "error_code"],
)
EVOLUTION_PROVIDER_HEALTH_TOTAL = Counter(
    "evolution_provider_health_total",
    "Total Evolution provider health checks grouped by provider and status",
    ["provider", "status"],
)
SHELL_POOL_SIZE = Gauge("shell_pool_size", "Total size of the shell pool")
SHELL_POOL_BUSY = Gauge("shell_pool_busy", "Number of busy shells in the pool")
SHELL_POOL_FREE = Gauge("shell_pool_free", "Number of free shells in the pool")
RAG_RETRIEVAL_DURATION = Histogram("rag_retrieval_duration_seconds", "Duration of RAG retrieval calls")
RAG_CHUNKS_SELECTED = Histogram("rag_chunks_selected", "Number of chunks selected for context")
RAG_REQUESTS_TOTAL = Counter("rag_requests_total", "Total RAG requests", ["mode"])
KNOWLEDGE_INDEX_RUNS_TOTAL = Counter(
    "knowledge_index_runs_total",
    "Total knowledge index runs",
    ["scope", "status", "profile"],
)
KNOWLEDGE_INDEX_DURATION_SECONDS = Histogram(
    "knowledge_index_duration_seconds",
    "Duration of knowledge index runs",
    ["scope", "profile"],
)
KNOWLEDGE_INDEX_ACTIVE_JOBS = Gauge("knowledge_index_active_jobs", "Number of active knowledge index jobs")
KNOWLEDGE_RETRIEVAL_CHUNKS = Histogram(
    "knowledge_retrieval_chunks_selected",
    "Number of knowledge index chunks selected during retrieval",
)
RAG_BUNDLE_BUDGET_UTILIZATION = Histogram(
    "rag_bundle_budget_utilization",
    "Retrieval context budget utilization per bundle",
    ["task_kind", "bundle_mode"],
)
RAG_BUNDLE_DUPLICATE_RATE = Histogram(
    "rag_bundle_duplicate_rate",
    "Duplicate candidate rate observed during retrieval fusion",
    ["task_kind", "bundle_mode"],
)
RAG_BUNDLE_NOISE_RATE = Histogram(
    "rag_bundle_noise_rate",
    "Estimated noise rate in selected retrieval bundles",
    ["task_kind", "bundle_mode"],
)
RAG_RETRIEVAL_TASK_KIND_TOTAL = Counter(
    "rag_retrieval_task_kind_total",
    "Total retrieval requests grouped by task kind and retrieval outcome",
    ["task_kind", "bundle_mode", "outcome"],
)
TASK_KIND_ROUTING_OUTCOME_TOTAL = Counter(
    "task_kind_routing_outcome_total",
    "Total policy and routing outcomes grouped by task kind, policy and status",
    ["task_kind", "policy_name", "status"],
)
TASK_KIND_VERIFICATION_OUTCOME_TOTAL = Counter(
    "task_kind_verification_outcome_total",
    "Total verification outcomes grouped by task kind, verification type and status",
    ["task_kind", "verification_type", "status"],
)
CONTEXT_EFFICIENCY_BUDGET_UTILIZATION = Histogram(
    "context_efficiency_budget_utilization",
    "Context budget utilization grouped by task kind and final task status",
    ["task_kind", "task_status"],
)

# Ressourcen Metriken
APP_STARTUP_DURATION = Gauge("app_startup_duration_seconds", "Duration of the app startup process")
CPU_USAGE = Gauge("process_cpu_usage_percent", "CPU usage of the agent process")
RAM_USAGE = Gauge("process_ram_usage_bytes", "RAM usage of the agent process")
