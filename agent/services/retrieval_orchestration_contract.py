from __future__ import annotations


ORCHESTRATION_CONTRACT_VERSION = "retrieval-orchestration-v1"


def build_retrieval_orchestration_contract(*, entrypoint_group: str) -> dict[str, object]:
    return {
        "version": ORCHESTRATION_CONTRACT_VERSION,
        "entrypoint_group": str(entrypoint_group or "knowledge"),
        "ownership": {
            "hub_owned": [
                "request_validation",
                "source_policy_resolution",
                "job_state_transitions",
                "failure_classification",
                "retry_triggering",
                "result_traceability",
            ],
            "worker_executed": [
                "source_import",
                "source_indexing",
                "source_query_execution",
            ],
            "forbidden": ["worker_to_worker_orchestration", "implicit_cross_container_shared_state"],
        },
        "state_machine": {
            "states": ["queued", "running", "completed", "failed"],
            "transitions": [
                {"from": "queued", "to": "running", "owner": "hub"},
                {"from": "running", "to": "completed", "owner": "hub"},
                {"from": "running", "to": "failed", "owner": "hub"},
            ],
            "retry_contract": {
                "coordinator": "hub",
                "trigger": "explicit_reindex_or_retry_api_call",
                "notes": [
                    "workers emit result status only",
                    "hub keeps auditable lifecycle state",
                ],
            },
        },
        "flows": {
            "import": ["hub_validate", "hub_create_task", "worker_ingest", "hub_persist_state"],
            "index": ["hub_enqueue", "worker_index", "hub_finalize_run", "hub_expose_status"],
            "query": ["hub_policy", "worker_retrieve", "hub_fuse_and_redact", "hub_return_response"],
        },
    }
