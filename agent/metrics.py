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
TASK_QUEUE_WAIT_SECONDS = Histogram("task_queue_wait_seconds", "Time task spent waiting in queue before dispatch")
DISPATCH_WAIT_SECONDS = Histogram("dispatch_wait_seconds", "Time spent waiting for dispatch completion")
WORKER_PROPOSE_DURATION_SECONDS = Histogram("worker_propose_duration_seconds", "Duration of worker propose calls")
STRATEGY_ATTEMPT_COUNT = Histogram("strategy_attempt_count", "Number of strategy attempts per task")
WORKER_BUSY_SECONDS = Histogram("worker_busy_seconds", "Observed busy-time of worker-dispatched tasks")
TASK_SUCCESS_RATE = Counter("task_success_total", "Successful task executions")
TASK_FAILURE_REASON_COUNT = Counter("task_failure_reason_total", "Task failures by reason", ["reason"])
WORKSPACE_WRITE_CONFLICT_COUNT = Counter("workspace_write_conflict_total", "Workspace write conflicts")
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
# Vector-store labels are deliberately bounded in
# agent.adapters.vector_store_metrics_adapter. Never add collection, workspace,
# repository, profile, file, payload or vector values as labels.
VECTOR_STORE_OPERATIONS_TOTAL = Counter(
    "vector_store_operations_total",
    "Vector-store operations grouped by bounded backend, operation and outcome",
    ["backend", "operation", "outcome", "reason_code"],
)
VECTOR_STORE_OPERATION_DURATION_SECONDS = Histogram(
    "vector_store_operation_duration_seconds",
    "Vector-store operation duration",
    ["backend", "operation", "outcome"],
)
VECTOR_STORE_ITEMS_TOTAL = Counter(
    "vector_store_items_total",
    "Items processed by vector-store operations",
    ["backend", "operation", "outcome", "count_kind"],
)
VECTOR_STORE_FALLBACKS_TOTAL = Counter(
    "vector_store_fallbacks_total",
    "Explicit vector-store provider fallbacks",
    ["requested_backend", "effective_backend", "reason_code"],
)
# Mail labels are bounded by agent.adapters.mail_metrics_adapter. Account,
# mailbox, message, URL, tenant and free-form reason values are forbidden.
MAIL_PROVIDER_CALLS_TOTAL = Counter(
    "mail_provider_calls_total",
    "Mail provider calls grouped by bounded provider, operation and outcome",
    ["provider", "operation", "outcome", "error_class"],
)
MAIL_PROVIDER_CALL_DURATION_SECONDS = Histogram(
    "mail_provider_call_duration_seconds",
    "Mail provider call duration",
    ["provider", "operation", "outcome"],
)
MAIL_PROVIDER_RETRIES_TOTAL = Counter(
    "mail_provider_retries_total",
    "Mail provider retries grouped by bounded error class",
    ["provider", "operation", "error_class"],
)
MAIL_SYNC_CHANGES_TOTAL = Counter(
    "mail_sync_changes_total",
    "Mail synchronization changes grouped by bounded change kind",
    ["provider", "change_kind"],
)
MAIL_CIRCUIT_STATE = Gauge(
    "mail_circuit_state",
    "Mail provider circuit state as a bounded one-hot gauge",
    ["provider", "state"],
)
VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL = Counter(
    "visual_process_assistant_requests_total",
    "Visual Process Assistant lifecycle transitions",
    ["status"],
)
VISUAL_PROCESS_ASSISTANT_ACTIVE = Gauge(
    "visual_process_assistant_active",
    "Current Visual Process Assistant requests in a non-terminal state",
)
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
CODECOMPASS_FILE_TYPE_FILES_TOTAL = Counter(
    "codecompass_file_type_files_total",
    "CodeCompass file outcomes grouped by bounded registry type",
    ["pipeline", "format_id", "outcome"],
)
CODECOMPASS_FILE_TYPE_FALLBACKS_TOTAL = Counter(
    "codecompass_file_type_fallbacks_total",
    "CodeCompass parser fallbacks grouped by bounded reason code",
    ["pipeline", "format_id", "reason_code"],
)
CODECOMPASS_FILE_TYPE_DIAGNOSTICS_TOTAL = Counter(
    "codecompass_file_type_diagnostics_total",
    "CodeCompass parser diagnostics grouped by bounded diagnostic code",
    ["pipeline", "format_id", "diagnostic_code"],
)
CODECOMPASS_FILE_TYPE_DURATION_SECONDS = Histogram(
    "codecompass_file_type_duration_seconds",
    "CodeCompass parsing duration per registry type",
    ["pipeline", "format_id", "outcome"],
)
CODECOMPASS_FILE_TYPE_BYTES = Histogram(
    "codecompass_file_type_bytes",
    "CodeCompass input bytes per registry type",
    ["pipeline", "format_id"],
)
CODECOMPASS_FILE_TYPE_SYMBOLS = Histogram(
    "codecompass_file_type_symbols",
    "CodeCompass symbols emitted per registry type",
    ["pipeline", "format_id"],
)
CODECOMPASS_FILE_TYPE_EDGES = Histogram(
    "codecompass_file_type_edges",
    "CodeCompass relationship edges emitted per registry type",
    ["pipeline", "format_id"],
)
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

# Source-Control labels are closed and normalized by
# agent.adapters.source_control_metrics_adapter. Object, tenant, project,
# actor, trace, URL, path and credential values must never become labels.
SOURCE_CONTROL_OPERATIONS_TOTAL = Counter(
    "source_control_operations_total",
    "Canonical Source Control Center operations by bounded outcome",
    ["operation", "decision", "reason_code", "status"],
)
SOURCE_CONTROL_OPERATION_DURATION_SECONDS = Histogram(
    "source_control_operation_duration_seconds",
    "Canonical Source Control Center operation duration",
    ["operation", "status"],
)
SOURCE_CONTROL_HEALTH = Gauge(
    "source_control_health",
    "Source Control Center health as a bounded one-hot gauge",
    ["status"],
)
SOURCE_CONTROL_ALERT_STATE = Gauge(
    "source_control_alert_state",
    "Source Control Center bounded alarm state",
    ["reason_code", "status"],
)
SOURCE_CONTROL_SHADOW_DIFFERENCES_TOTAL = Counter(
    "source_control_shadow_differences_total",
    "Content-free Source Control Center shadow comparison outcomes",
    ["operation", "decision", "status"],
)

# Voice labels are deliberately bounded enumerations. Request IDs, tenants,
# filenames, transcript text and model paths must never become metric labels.
VOICE_HUB_REQUESTS_TOTAL = Counter(
    "voice_hub_requests_total",
    "Hub-mediated voice requests grouped by operation and bounded outcome",
    ["operation", "outcome", "error_code"],
)
VOICE_HUB_DURATION_SECONDS = Histogram(
    "voice_hub_duration_seconds",
    "Hub-mediated voice request duration",
    ["operation", "outcome"],
)
VOICE_AUDIO_DURATION_SECONDS = Histogram(
    "voice_audio_duration_seconds",
    "Recognized audio duration grouped by bounded backend label",
    ["backend"],
)
VOICE_REAL_TIME_FACTOR = Histogram(
    "voice_real_time_factor",
    "Voice candidate real-time factor grouped by bounded backend label",
    ["backend"],
)
VOICE_FALLBACK_TOTAL = Counter(
    "voice_fallback_total",
    "Voice fallback decisions grouped by bounded backend and reason",
    ["backend", "reason_code"],
)
VOICE_RERUN_TOTAL = Counter(
    "voice_rerun_total",
    "Voice confidence rerun outcomes grouped by bounded backend and outcome",
    ["backend", "outcome"],
)
VOICE_STREAM_EVENTS_TOTAL = Counter(
    "voice_stream_events_total",
    "Hub voice streaming events grouped by bounded event type and outcome",
    ["event_type", "outcome"],
)
VOICE_BACKPRESSURE_TOTAL = Counter(
    "voice_backpressure_total",
    "Voice backpressure responses grouped by internal surface",
    ["surface"],
)

# Ressourcen Metriken
APP_STARTUP_DURATION = Gauge("app_startup_duration_seconds", "Duration of the app startup process")
APP_STARTUP_PHASE_DURATION = Histogram(
    "app_startup_phase_duration_seconds",
    "Duration of individual app startup phases",
    ["phase", "status"],
)
APP_STARTUP_PHASE_TOTAL = Counter(
    "app_startup_phase_total",
    "Total startup phase executions grouped by phase and status",
    ["phase", "status"],
)
APP_STARTUP_FAILURES_TOTAL = Counter(
    "app_startup_failures_total",
    "Total startup phase failures grouped by phase and error type",
    ["phase", "error_type"],
)
CPU_USAGE = Gauge("process_cpu_usage_percent", "CPU usage of the agent process")
RAM_USAGE = Gauge("process_ram_usage_bytes", "RAM usage of the agent process")
