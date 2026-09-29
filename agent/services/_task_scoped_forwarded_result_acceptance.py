"""Hub-side admission of handler-owned forwarded worker results.

Split out of ``_task_scoped_forwarding`` (SRP): the visual-process-assistant
result contracts and acceptor, and the acceptors for local runtime capability
and CodeCompass layer results. ``_task_scoped_forwarding`` re-exports every
name; patchable collaborators are resolved through that facade at call time.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _facade():
    """Resolve patchable collaborators through the public ``_task_scoped_forwarding`` entry point."""
    import agent.services._task_scoped_forwarding as facade_module

    return facade_module


_VISUAL_PROCESS_ASSISTANT_RESULT_CONTRACTS: dict[str, tuple[str, frozenset[str]]] = {
    "visual_process_assistant_retrieval": (
        "ananta.visual_process_assistant.retrieval_result.v1",
        frozenset(
            {
                "schema",
                "task_id",
                "request_id",
                "context_id",
                "status",
                "evidence",
                "rejected_count",
                "rejection_reasons",
                "consistency_state",
                "blocked_stubs",
                "evidence_conflicts",
            }
        ),
    ),
    "visual_process_assistant_inference": (
        "ananta.visual_process_assistant.inference_result.v1",
        frozenset(
            {
                "schema",
                "task_id",
                "request_id",
                "context_id",
                "prompt_hash",
                "status",
                "reason_code",
                "response",
                "model_metadata",
            }
        ),
    ),
}


_VISUAL_PROCESS_ASSISTANT_RESULT_SCHEMAS = frozenset(
    schema for schema, _fields in _VISUAL_PROCESS_ASSISTANT_RESULT_CONTRACTS.values()
)


_FORWARDED_HANDLER_FRAMEWORK_FIELDS = frozenset({"handler_contract"})


def _get_visual_process_assistant_service() -> Any:
    """Resolve the Hub-owned service lazily and avoid a forwarding import cycle."""

    from agent.services.visual_process_assistant_service import (
        visual_process_assistant_service,
    )

    return visual_process_assistant_service


def _accept_visual_process_assistant_result(
    *,
    tid: str,
    response: Mapping[str, Any],
    task: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Dispatch a bound Visual Process worker result to its Hub owner.

    A Visual Process schema or task kind activates this fail-closed boundary.
    Framework metadata is validated separately and is never passed into the
    domain result contract.
    """

    task_kind = str(task.get("task_kind") or "").strip().lower()
    schema = str(response.get("schema") or "").strip()
    expected = _VISUAL_PROCESS_ASSISTANT_RESULT_CONTRACTS.get(task_kind)
    if expected is None and schema not in _VISUAL_PROCESS_ASSISTANT_RESULT_SCHEMAS:
        return None
    if expected is None:
        raise ValueError("visual_process_assistant_result_task_kind_unknown")
    expected_schema, result_fields = expected
    if schema != expected_schema:
        raise ValueError("visual_process_assistant_result_schema_kind_mismatch")

    handler_contract = response.get("handler_contract")
    if handler_contract is not None:
        if not isinstance(handler_contract, Mapping):
            raise ValueError("visual_process_assistant_handler_contract_invalid")
        handler_task_kind = str(handler_contract.get("task_kind") or "").strip().lower()
        if handler_task_kind != task_kind:
            raise ValueError("visual_process_assistant_handler_task_kind_mismatch")

    unknown_fields = set(response) - result_fields - _FORWARDED_HANDLER_FRAMEWORK_FIELDS
    if unknown_fields:
        raise ValueError("visual_process_assistant_result_forwarding_fields_unknown")
    candidate = {field: response.get(field) for field in result_fields}
    accepted = _facade()._get_visual_process_assistant_service().accept_worker_result(
        task_id=tid,
        result=candidate,
    )
    if not isinstance(accepted, Mapping):
        raise ValueError("visual_process_assistant_acceptance_readmodel_invalid")
    return dict(accepted)


def _accept_local_runtime_capability_result(
    *,
    task: Mapping[str, Any],
    response: Mapping[str, Any],
) -> dict[str, Any]:
    if str(task.get("task_kind") or "").strip() != "local_runtime_capability_refresh":
        return {}
    from agent.services.local_runtime_capability_composition import (
        local_runtime_capability_cache,
    )
    from agent.services.local_runtime_capability_result_ingestor import (
        LocalRuntimeCapabilityResultIngestor,
    )

    accepted = LocalRuntimeCapabilityResultIngestor(
        local_runtime_capability_cache()
    ).accept(task=task, response=response)
    return {"local_runtime_capability_refresh": accepted} if accepted is not None else {}


def _accept_codecompass_layer_result(
    *,
    tid: str,
    task: Mapping[str, Any],
    response: Mapping[str, Any],
) -> dict[str, Any]:
    """Admit a delegated layer build only for the task it was dispatched as."""
    from ananta_contracts.codecompass_layer_job import TASK_KIND

    if str(task.get("task_kind") or "").strip() != TASK_KIND:
        return {}
    from agent.services.codecompass_layer_service import get_codecompass_layer_service

    if str(response.get("task_id") or "") != str(tid):
        return {TASK_KIND: {"status": "rejected", "reason_code": "codecompass_layer_result_task_mismatch"}}
    try:
        return {TASK_KIND: get_codecompass_layer_service().admit_result(response)}
    except (RuntimeError, ValueError) as error:
        return {TASK_KIND: {"status": "rejected", "reason_code": str(error)[:160]}}
