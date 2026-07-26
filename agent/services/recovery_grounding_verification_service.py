"""Hub-owned grounding gate for fenced Recovery Worker results."""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Mapping
from typing import Any, Callable

_SOURCE_ID_PATTERN = re.compile(r"^(?:SRC|RUN)_[0-9]{4}$")
_RUN_ID_PATTERN = re.compile(r"^RUN_[0-9]{4}$")
_MENTIONED_SOURCE_ID_PATTERN = re.compile(
    r"\b(?:SRC|RUN)_[A-Za-z0-9][A-Za-z0-9_.:-]*"
)
_SOURCE_TYPES = frozenset(
    {
        "rag_chunk",
        "repo_file",
        "artifact",
        "wiki_chunk",
        "tool_run",
        "test_result",
        "generated_artifact",
    }
)
_RUN_SOURCE_TYPES = frozenset(
    {"tool_run", "test_result", "generated_artifact"}
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _task_mapping(task: Any) -> dict[str, Any]:
    if isinstance(task, Mapping):
        return dict(task)
    serializer = getattr(task, "model_dump", None)
    if callable(serializer):
        value = serializer()
        if isinstance(value, Mapping):
            return dict(value)
    try:
        return dict(vars(task))
    except (TypeError, ValueError):
        return {}


class RecoveryGroundingVerificationService:
    """Re-verify Recovery output against evidence persisted by the Hub.

    Worker-provided verification verdicts are deliberately ignored. The
    allowlist is resolved from the authoritative Task record, and the existing
    citation verifier derives a fresh Hub-side verdict from that allowlist.
    """

    def __init__(
        self,
        *,
        citation_verification_service_provider: (
            Callable[[], Any] | None
        ) = None,
        hub_run_evidence_provider: (
            Callable[
                [str],
                list[dict[str, Any]] | None,
            ]
            | None
        ) = None,
    ) -> None:
        self._citation_verification_service_provider = (
            citation_verification_service_provider
        )
        self._hub_run_evidence_provider = (
            hub_run_evidence_provider
        )

    def _citation_verification(self) -> Any:
        if self._citation_verification_service_provider is not None:
            return self._citation_verification_service_provider()
        from agent.services.citation_verification_service import (
            get_citation_verification_service,
        )

        return get_citation_verification_service()

    @staticmethod
    def _worker_projection(
        verification_status: Mapping[str, Any],
        *,
        task_id: str,
        phase: str,
    ) -> dict[str, Any]:
        results = _mapping(
            verification_status.get("recovery_worker_results")
        )
        envelope = results.get(phase)
        if not isinstance(envelope, Mapping):
            return {}
        from agent.services.recovery_worker_result_service import (
            RecoveryWorkerResultError,
            get_recovery_worker_result_service,
        )

        try:
            validated = (
                get_recovery_worker_result_service().validate(
                    envelope,
                    task_id=task_id,
                    phase=phase,
                )
            )
        except RecoveryWorkerResultError:
            return {}
        return _mapping(validated.get("verification_projection"))

    @staticmethod
    def _release_binding_error(task: Any) -> str | None:
        """Require the Hub-approved payload digest before reading context."""

        from agent.services.recovery_plan_contract import (
            calculate_recovery_task_payload_digest,
        )

        release = _mapping(
            _mapping(
                _value(task, "status_reason_details")
            ).get("model_recovery_release")
        )
        required_release_values = (
            "release_epoch",
            "plan_id",
            "source_task_id",
            "goal_id",
            "approval_request_id",
            "recovery_key",
        )
        if (
            str(release.get("schema") or "")
            != "ananta.recovery_release_gate.v1"
            or any(
                not str(release.get(key) or "").strip()
                for key in required_release_values
            )
            or "team_id" not in release
        ):
            return "recovery_task_payload_binding_invalid"
        for release_key, task_field in (
            ("plan_id", "plan_id"),
            ("source_task_id", "source_task_id"),
            ("goal_id", "goal_id"),
            ("team_id", "team_id"),
        ):
            if str(release.get(release_key) or "") != str(
                _value(task, task_field) or ""
            ):
                return "recovery_task_payload_binding_invalid"
        expected_digest = str(
            release.get("task_payload_digest") or ""
        ).strip()
        actual_digest = calculate_recovery_task_payload_digest(
            task
        )
        if (
            len(expected_digest) != 64
            or not hmac.compare_digest(
                expected_digest,
                actual_digest,
            )
        ):
            return "recovery_task_payload_binding_invalid"
        return None

    @staticmethod
    def _catalog_status_projection(
        catalog: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Mirror the persisted status projection produced at propose time."""

        sources = [
            dict(value)
            for value in list(catalog.get("sources") or [])
            if isinstance(value, Mapping)
        ]
        rejected = [
            dict(value)
            for value in list(
                catalog.get("rejected_candidates") or []
            )
            if isinstance(value, Mapping)
        ]
        return {
            "schema": catalog.get("schema"),
            "source_catalog_id": catalog.get("catalog_id"),
            "source_catalog_hash": catalog.get("catalog_hash"),
            "catalog_state": catalog.get("catalog_state"),
            "source_count": len(sources),
            "rejected_count": len(rejected),
            "retrieval_trace_id": catalog.get(
                "retrieval_trace_id"
            ),
            "retrieval_context_hash": catalog.get(
                "retrieval_context_hash"
            ),
            "retrieval_manifest_hash": catalog.get(
                "retrieval_manifest_hash"
            ),
            "sources": sources,
        }

    @staticmethod
    def _hub_source_catalog(
        task: Any,
    ) -> tuple[dict[str, Any], str | None]:
        from agent.services._task_scoped_citation import (
            build_source_catalog_from_execution_context,
        )

        task_id = str(_value(task, "id") or "").strip()
        try:
            value = build_source_catalog_from_execution_context(
                tid=task_id,
                task=_task_mapping(task),
                llm_scope="local_only",
            )
        except Exception:
            return {}, "recovery_hub_source_catalog_invalid"
        if value is None:
            return {}, None
        if not isinstance(value, Mapping):
            return {}, "recovery_hub_source_catalog_invalid"
        return dict(value), None

    @classmethod
    def _candidate_source_catalog(
        cls,
        *,
        task_id: str,
        verification_status: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        direct = verification_status.get("source_catalog")
        if direct is not None and not isinstance(direct, Mapping):
            return {}, "recovery_worker_source_catalog_invalid"
        direct_catalog = _mapping(direct)
        proposal_projection = cls._worker_projection(
            verification_status,
            task_id=task_id,
            phase="propose",
        )
        proposal_catalog = _mapping(
            proposal_projection.get("source_catalog")
        )
        if (
            direct_catalog
            and proposal_catalog
            and direct_catalog != proposal_catalog
        ):
            return {}, "recovery_worker_source_catalog_mismatch"
        return direct_catalog or proposal_catalog, None

    def _hub_run_evidence(
        self,
        task_id: str,
    ) -> tuple[list[Any], bool, str | None]:
        """Use only an explicitly injected Hub persistence port."""

        provider = self._hub_run_evidence_provider
        if provider is None:
            from agent.services.recovery_hub_run_evidence_service import (
                get_recovery_hub_run_evidence_service,
            )

            provider = (
                get_recovery_hub_run_evidence_service().for_task
            )
        try:
            value = provider(task_id)
        except Exception:
            return [], True, "recovery_hub_run_evidence_invalid"
        if value is None:
            return [], False, None
        if not isinstance(value, list):
            return [], True, "recovery_hub_run_evidence_invalid"
        return list(value), True, None

    def _authoritative_context(
        self,
        task: Any,
    ) -> tuple[
        dict[str, Any],
        list[Any],
        str,
        str | None,
        bool,
    ]:
        """Resolve allowlists only from digest-bound Hub-owned inputs."""

        binding_error = self._release_binding_error(task)
        if binding_error is not None:
            return {}, [], "missing", binding_error, False

        task_id = str(_value(task, "id") or "").strip()
        verification_status = _mapping(
            _value(task, "verification_status")
        )
        hub_catalog, hub_catalog_error = (
            self._hub_source_catalog(task)
        )
        candidate_catalog, candidate_error = (
            self._candidate_source_catalog(
                task_id=task_id,
                verification_status=verification_status,
            )
        )
        resolution_error = hub_catalog_error or candidate_error
        if candidate_catalog:
            if not hub_catalog:
                resolution_error = (
                    resolution_error
                    or "recovery_hub_source_catalog_missing"
                )
            elif candidate_catalog != self._catalog_status_projection(
                hub_catalog
            ):
                resolution_error = (
                    resolution_error
                    or "recovery_worker_source_catalog_mismatch"
                )

        hub_runs, run_authority_available, run_error = (
            self._hub_run_evidence(task_id)
        )
        return (
            hub_catalog,
            hub_runs,
            (
                "hub_worker_execution_context"
                if hub_catalog
                else "hub_worker_execution_context_empty"
            ),
            resolution_error or run_error,
            run_authority_available,
        )

    @staticmethod
    def _validate_context(
        *,
        task_id: str,
        source_catalog: Mapping[str, Any],
        tool_run_refs: list[Any],
    ) -> tuple[str | None, set[str], set[str]]:
        source_ids: set[str] = set()
        run_ids: set[str] = set()
        if source_catalog and "sources" not in source_catalog:
            return "recovery_source_catalog_invalid", source_ids, run_ids
        raw_sources = (
            source_catalog.get("sources")
            if "sources" in source_catalog
            else []
        )
        if not isinstance(raw_sources, list):
            return "recovery_source_catalog_invalid", source_ids, run_ids
        catalog_schema = str(source_catalog.get("schema") or "")
        if source_catalog and catalog_schema not in {
            "source_catalog.v1",
            "source_catalog.v2",
        }:
            return "recovery_source_catalog_invalid", source_ids, run_ids
        source_count = source_catalog.get("source_count")
        if (
            source_count is not None
            and (
                not isinstance(source_count, int)
                or isinstance(source_count, bool)
                or source_count != len(raw_sources)
            )
        ):
            return "recovery_source_catalog_invalid", source_ids, run_ids

        for value in raw_sources:
            if not isinstance(value, Mapping):
                return (
                    "recovery_source_catalog_invalid",
                    source_ids,
                    run_ids,
                )
            row = dict(value)
            source_id = str(row.get("source_id") or "").strip()
            source_type = str(row.get("source_type") or "")
            source_is_run_evidence = (
                source_type in _RUN_SOURCE_TYPES
            )
            if (
                _SOURCE_ID_PATTERN.fullmatch(source_id) is None
                or source_id in source_ids
                or str(row.get("task_id") or "").strip() != task_id
                or source_type not in _SOURCE_TYPES
                or source_id.startswith("RUN_")
                or source_is_run_evidence
                or not isinstance(
                    row.get("allowed_for_llm_scope"),
                    bool,
                )
            ):
                return (
                    "recovery_source_catalog_invalid",
                    source_ids,
                    run_ids,
                )
            source_ref = row.get("source_ref")
            if (
                source_ref is not None
                and (
                    not isinstance(source_ref, Mapping)
                    or str(source_ref.get("source_id") or "")
                    != source_id
                )
            ):
                return (
                    "recovery_source_catalog_invalid",
                    source_ids,
                    run_ids,
                )
            source_ids.add(source_id)

        for row in tool_run_refs:
            if not isinstance(row, Mapping):
                return (
                    "recovery_tool_run_catalog_invalid",
                    source_ids,
                    run_ids,
                )
            run_id = str(row.get("source_id") or "").strip()
            if (
                _RUN_ID_PATTERN.fullmatch(run_id) is None
                or run_id in run_ids
                or run_id in source_ids
                or str(row.get("task_id") or "").strip() != task_id
                or str(row.get("source_type") or "")
                not in _RUN_SOURCE_TYPES
                or row.get("allowed_for_llm_scope") is not True
            ):
                return (
                    "recovery_tool_run_catalog_invalid",
                    source_ids,
                    run_ids,
                )
            run_ids.add(run_id)
        return None, source_ids, run_ids

    def verify(
        self,
        *,
        task: Any,
        output: str | None,
        artifacts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        task_id = str(_value(task, "id") or "").strip()
        (
            source_catalog,
            tool_run_refs,
            source_origin,
            resolution_error,
            run_authority_available,
        ) = self._authoritative_context(task)
        context_error, source_ids, run_ids = self._validate_context(
            task_id=task_id,
            source_catalog=source_catalog,
            tool_run_refs=tool_run_refs,
        )
        context_error = resolution_error or context_error
        provided_ids = source_ids | run_ids
        raw_output = str(output or "").strip()
        verified_artifact_present = any(
            bool(value.get("_exists"))
            and bool(value.get("_hash_verified"))
            for value in artifacts
        )
        result: dict[str, Any] = {
            "status": "failed",
            "passed": False,
            "reason_code": (
                context_error or "recovery_grounding_not_evaluated"
            ),
            "source_catalog_origin": source_origin,
            "provided_source_ids": sorted(source_ids),
            "provided_run_ids": sorted(run_ids),
            "verified_artifact_present": (
                verified_artifact_present
            ),
            "hub_run_authority_available": (
                run_authority_available
            ),
            "citation_verification": {
                "status": "not_evaluated",
                "verified_claim_count": 0,
                "unverified_claim_count": 0,
                "failed_claims": [],
            },
        }
        if context_error:
            return result

        mentioned_ids = set(
            _MENTIONED_SOURCE_ID_PATTERN.findall(raw_output)
        )
        unknown_mentions = sorted(mentioned_ids - provided_ids)
        if unknown_mentions:
            unknown_run_ids = [
                value
                for value in unknown_mentions
                if value.startswith("RUN_")
            ]
            result["reason_code"] = (
                "recovery_hub_run_evidence_missing"
                if unknown_run_ids
                and not run_authority_available
                else "recovery_output_unknown_source_id"
            )
            result["unknown_source_ids"] = unknown_mentions
            return result

        answer_payload: dict[str, Any] | None = None
        if raw_output:
            try:
                parsed = json.loads(raw_output)
            except (TypeError, ValueError):
                parsed = None
            if (
                isinstance(parsed, dict)
                and str(parsed.get("schema") or "")
                == "grounded_answer.v1"
            ):
                answer_payload = parsed

        if answer_payload is not None:
            citation_result = self._citation_verification().verify(
                task_id=task_id,
                answer_payload=answer_payload,
                source_catalog={
                    **source_catalog,
                    "sources": [
                        dict(value)
                        for value in list(
                            source_catalog.get("sources") or []
                        )
                        if isinstance(value, Mapping)
                    ],
                },
                tool_run_catalog=[
                    dict(value)
                    for value in tool_run_refs
                    if isinstance(value, Mapping)
                ],
            )
            result["citation_verification"] = dict(
                citation_result
            )
            if (
                str(citation_result.get("status") or "")
                != "verified"
            ):
                result["reason_code"] = (
                    str(citation_result.get("status") or "")
                    or "recovery_citation_verification_failed"
                )
                return result
            if (
                int(
                    citation_result.get(
                        "verified_claim_count"
                    )
                    or 0
                )
                > 0
            ):
                result.update(
                    {
                        "status": "passed",
                        "passed": True,
                        "reason_code": (
                            "recovery_citations_verified"
                        ),
                    }
                )
                return result

        if verified_artifact_present:
            result.update(
                {
                    "status": "passed",
                    "passed": True,
                    "reason_code": (
                        "recovery_artifact_evidence_verified"
                    ),
                }
            )
            return result

        result["reason_code"] = (
            "recovery_result_evidence_missing"
            if not raw_output
            else "recovery_grounded_answer_required"
        )
        return result


_service = RecoveryGroundingVerificationService()


def get_recovery_grounding_verification_service() -> (
    RecoveryGroundingVerificationService
):
    return _service


__all__ = [
    "RecoveryGroundingVerificationService",
    "get_recovery_grounding_verification_service",
]
