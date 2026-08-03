"""Worker-local admission for one Hub-delegated Organization research Task."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from types import SimpleNamespace
from typing import Any

from sqlmodel import Session

from agent.db_models import ContextBundleDB, TaskDB
from agent.services.organization_research_delegation_policy_service import (
    context_bundle_integrity_digest,
)
from agent.services.organization_research_dispatch_capability_service import (
    OrganizationResearchDispatchCapabilityError,
    OrganizationResearchDispatchCapabilityVerifier,
    organization_research_dispatch_payload_digest,
)

_CONTEXT_POLICY_SCHEMA = "organization_research_source_context_policy.v1"
_DESTINATION_BINDING_SCHEMA = "organization_research_destination_binding.v1"
_ADMISSION_SCHEMA = "organization_research_worker_admission.v1"
_DELEGATED_BUNDLE_SCHEMA = "organization_research_delegated_context.v1"
_REQUIRED_CAPABILITIES = frozenset(
    {"planning", "research", "source_analysis"}
)
_ALLOWED_PAYLOAD_FIELDS = frozenset(
    {
        "id",
        "title",
        "description",
        "parent_task_id",
        "priority",
        "team_id",
        "goal_id",
        "goal_trace_id",
        "task_kind",
        "retrieval_intent",
        "required_context_scope",
        "preferred_bundle_mode",
        "required_capabilities",
        "context_bundle_id",
        "worker_execution_context",
        "callback_url",
        "callback_token",
        "assignment_id",
        "dispatch_lease_id",
        "source",
        "created_by",
        "context_bundle_policy",
        "hub_dispatch_capability",
    }
)


class OrganizationResearchWorkerIntakeError(ValueError):
    """Stable fail-closed intake rejection."""

    def __init__(self, reason_code: str, *, status_code: int = 403) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


class OrganizationResearchWorkerIntakeService:
    """Verify Hub authority, clone context locally, and persist one Task."""

    def __init__(
        self,
        *,
        capability_verifier: (
            OrganizationResearchDispatchCapabilityVerifier | None
        ) = None,
        session_factory: Callable[[], Session] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._capability_verifier = capability_verifier
        self._session_factory = session_factory or self._default_session
        self._clock = clock

    @staticmethod
    def _default_session() -> Session:
        from agent.database import engine

        return Session(engine)

    def admit(
        self,
        payload: Mapping[str, Any],
        *,
        worker_url: str,
    ) -> dict[str, Any]:
        raw = dict(payload or {})
        unexpected = sorted(set(raw) - _ALLOWED_PAYLOAD_FIELDS)
        if unexpected:
            self._fail("organization_research_dispatch_fields_invalid")
        token = str(raw.get("hub_dispatch_capability") or "").strip()
        try:
            claims = self._verifier().verify(
                token,
                payload=raw,
                worker_url=str(worker_url or "").strip(),
            )
        except OrganizationResearchDispatchCapabilityError as exc:
            raise OrganizationResearchWorkerIntakeError(
                exc.reason_code
            ) from exc

        validated = self._validate_contract(
            raw,
            claims=claims,
            worker_url=str(worker_url or "").strip(),
        )
        task_id = validated["task_id"]
        payload_digest = str(claims["payload_digest"])
        clone_id = self._clone_bundle_id(
            task_id=task_id,
            origin_bundle_id=validated["origin_bundle_id"],
            origin_bundle_digest=validated["origin_bundle_digest"],
        )
        worker_context = self._worker_context(
            validated["worker_context"],
            clone_id=clone_id,
            claims=claims,
        )

        with self._session_factory() as session:
            try:
                existing_task = session.get(TaskDB, task_id)
                if existing_task is not None:
                    if not self._is_matching_replay(
                        existing_task,
                        payload_digest=payload_digest,
                        clone_id=clone_id,
                        claims=claims,
                    ):
                        self._fail(
                            "organization_research_dispatch_task_conflict",
                            status_code=409,
                        )
                    return {
                        "accepted": True,
                        "replayed": True,
                        "task_id": task_id,
                        "context_bundle_id": clone_id,
                    }

                clone = session.get(ContextBundleDB, clone_id)
                if clone is None:
                    clone = ContextBundleDB(
                        id=clone_id,
                        task_id=task_id,
                        bundle_type="worker_execution_context",
                        context_text=validated["context_text"],
                        chunks=validated["chunks"],
                        token_estimate=validated["token_estimate"],
                        bundle_metadata=self._clone_metadata(
                            validated["bundle_metadata"],
                            claims=claims,
                        ),
                    )
                    session.add(clone)
                elif not self._is_matching_clone(
                    clone,
                    task_id=task_id,
                    validated=validated,
                    payload_digest=payload_digest,
                    claims=claims,
                ):
                    self._fail(
                        "organization_research_dispatch_context_conflict",
                        status_code=409,
                    )

                now = float(self._clock())
                session.add(
                    TaskDB(
                        id=task_id,
                        title=str(raw.get("title") or "")[:200] or None,
                        description=str(raw.get("description") or "") or None,
                        status="created",
                        priority=str(raw.get("priority") or "High"),
                        team_id=None,
                        goal_id=str(raw.get("goal_id") or "").strip() or None,
                        goal_trace_id=(
                            str(raw.get("goal_trace_id") or "").strip()
                            or None
                        ),
                        task_kind="planning_research",
                        retrieval_intent=str(
                            raw.get("retrieval_intent") or ""
                        ).strip()
                        or None,
                        required_context_scope=str(
                            raw.get("required_context_scope") or ""
                        ).strip()
                        or None,
                        preferred_bundle_mode=str(
                            raw.get("preferred_bundle_mode") or ""
                        ).strip()
                        or None,
                        required_capabilities=sorted(
                            {
                                str(value).strip()
                                for value in list(
                                    raw.get("required_capabilities") or []
                                )
                                if str(value).strip()
                            }
                        ),
                        context_bundle_id=clone_id,
                        worker_execution_context=worker_context,
                        current_worker_job_id=str(
                            claims.get("worker_job_id") or ""
                        ),
                        callback_url=str(raw.get("callback_url") or "").strip()
                        or None,
                        callback_token=str(
                            raw.get("callback_token") or ""
                        ).strip()
                        or None,
                        parent_task_id=str(
                            raw.get("parent_task_id") or ""
                        ).strip()
                        or None,
                        source_task_id=str(
                            raw.get("parent_task_id") or ""
                        ).strip()
                        or None,
                        derivation_reason="hub_organization_planning_research",
                        derivation_depth=1,
                        history=[
                            {
                                "timestamp": now,
                                "status": "created",
                                "event_type": (
                                    "organization_research_dispatch_admitted"
                                ),
                                "actor": "hub:delegation",
                                "details": {
                                    "source": "agent",
                                    "channel": (
                                        "hub_organization_research_intake"
                                    ),
                                    "parent_task_id": claims[
                                        "parent_task_id"
                                    ],
                                    "worker_job_id": claims[
                                        "worker_job_id"
                                    ],
                                    "payload_digest": payload_digest,
                                },
                            }
                        ],
                    )
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

        return {
            "accepted": True,
            "replayed": False,
            "task_id": task_id,
            "context_bundle_id": clone_id,
        }

    def _validate_contract(
        self,
        payload: Mapping[str, Any],
        *,
        claims: Mapping[str, Any],
        worker_url: str,
    ) -> dict[str, Any]:
        task_id = str(payload.get("id") or "").strip()
        assignment_id = str(payload.get("assignment_id") or "").strip()
        dispatch_lease_id = str(
            payload.get("dispatch_lease_id") or ""
        ).strip()
        if (
            payload.get("task_kind") != "planning_research"
            or payload.get("source") != "agent"
            or task_id != assignment_id
            or task_id != str(claims.get("subtask_id") or "")
            or dispatch_lease_id
            != str(claims.get("worker_job_id") or "")
            or not str(payload.get("callback_url") or "").strip()
            or not str(payload.get("callback_token") or "").strip()
        ):
            self._fail("organization_research_dispatch_contract_invalid")

        capabilities = {
            str(value).strip()
            for value in list(payload.get("required_capabilities") or [])
            if str(value).strip()
        }
        if not _REQUIRED_CAPABILITIES.issubset(capabilities):
            self._fail(
                "organization_research_dispatch_capabilities_invalid"
            )

        worker_context = self._mapping(
            payload.get("worker_execution_context")
        )
        source_policy = self._mapping(
            worker_context.get("source_context_policy")
        )
        source_manifest = self._mapping(
            worker_context.get("source_context_bundle_manifest")
        )
        destination = self._mapping(
            worker_context.get("research_destination_binding")
        )
        context_policy = self._mapping(payload.get("context_bundle_policy"))
        origin_bundle_id = str(payload.get("context_bundle_id") or "").strip()
        origin_bundle_digest = str(
            source_policy.get("context_bundle_digest") or ""
        ).strip()
        if (
            source_policy.get("schema") != _CONTEXT_POLICY_SCHEMA
            or source_policy.get("authority") != "hub"
            or source_policy.get("mode")
            != "authoritative_source_catalog_bundle"
            or source_policy.get("llm_scope") != "local_only"
            or str(source_policy.get("context_bundle_id") or "")
            != origin_bundle_id
            or origin_bundle_id
            != str(claims.get("context_bundle_id") or "")
            or origin_bundle_digest
            != str(
                claims.get("source_context_bundle_digest") or ""
            )
        ):
            self._fail("organization_research_dispatch_source_invalid")
        if (
            destination.get("schema") != _DESTINATION_BINDING_SCHEMA
            or destination.get("llm_scope") != "local_only"
            or str(destination.get("worker_url") or "") != worker_url
            or str(destination.get("binding_digest") or "")
            != str(claims.get("destination_binding_digest") or "")
            or self._mapping(
                context_policy.get("research_destination_binding")
            )
            != destination
            or context_policy.get("llm_scope") != "local_only"
            or context_policy.get("mode")
            != "authoritative_source_catalog_bundle"
        ):
            self._fail(
                "organization_research_dispatch_destination_invalid"
            )

        proposal_binding = self._mapping(
            worker_context.get("task_proposal_binding")
        )
        assignment_binding = self._mapping(
            worker_context.get("planning_research_assignment")
        )
        if (
            str(proposal_binding.get("worker_id") or "") != worker_url
            or str(proposal_binding.get("assignment_id") or "") != task_id
            or str(proposal_binding.get("dispatch_lease_id") or "")
            != dispatch_lease_id
            or assignment_binding.get("schema")
            != "organization_category_research_assignment_binding.v1"
            or str(assignment_binding.get("assignment_id") or "")
            != str(claims.get("organization_assignment_id") or "")
            or str(assignment_binding.get("agent_url") or "")
            != worker_url
            or str(assignment_binding.get("organization_id") or "")
            != str(proposal_binding.get("organization_id") or "")
            or str(assignment_binding.get("unit_id") or "")
            != str(proposal_binding.get("unit_id") or "")
            or str(assignment_binding.get("team_id") or "")
            != str(proposal_binding.get("team_id") or "")
            or str(assignment_binding.get("role_slot_id") or "")
            != str(proposal_binding.get("role_slot_id") or "")
        ):
            self._fail(
                "organization_research_dispatch_assignment_invalid"
            )

        context = self._mapping(worker_context.get("context"))
        chunks = context.get("chunks")
        metadata = context.get("bundle_metadata")
        token_estimate = context.get("token_estimate")
        if (
            not isinstance(context.get("context_text"), str)
            or not isinstance(chunks, list)
            or not isinstance(metadata, Mapping)
            or isinstance(token_estimate, bool)
            or not isinstance(token_estimate, int)
            or token_estimate < 0
        ):
            self._fail("organization_research_dispatch_context_invalid")
        if (
            source_manifest.get("schema")
            != "organization_research_context_manifest.v1"
            or str(source_manifest.get("id") or "")
            != origin_bundle_id
            or str(source_manifest.get("task_id") or "")
            != str(claims.get("parent_task_id") or "")
            or str(source_manifest.get("bundle_type") or "")
            != "worker_execution_context"
            or not str(
                source_manifest.get("retrieval_run_id") or ""
            ).strip()
        ):
            self._fail(
                "organization_research_dispatch_context_manifest_invalid"
            )
        calculated_origin_digest = context_bundle_integrity_digest(
            SimpleNamespace(
                id=origin_bundle_id,
                retrieval_run_id=str(
                    source_manifest.get("retrieval_run_id") or ""
                ),
                task_id=str(source_manifest.get("task_id") or ""),
                bundle_type=str(
                    source_manifest.get("bundle_type") or ""
                ),
                context_text=str(context["context_text"]),
                chunks=list(chunks),
                token_estimate=int(token_estimate),
                bundle_metadata=dict(metadata),
            )
        )
        if calculated_origin_digest != origin_bundle_digest:
            self._fail(
                "organization_research_dispatch_context_digest_mismatch"
            )
        return {
            "task_id": task_id,
            "origin_bundle_id": origin_bundle_id,
            "origin_bundle_digest": origin_bundle_digest,
            "worker_context": dict(worker_context),
            "context_text": str(context["context_text"]),
            "chunks": list(chunks),
            "token_estimate": int(token_estimate),
            "bundle_metadata": dict(metadata),
        }

    @staticmethod
    def _worker_context(
        worker_context: Mapping[str, Any],
        *,
        clone_id: str,
        claims: Mapping[str, Any],
    ) -> dict[str, Any]:
        sanitized = {
            key: value
            for key, value in dict(worker_context).items()
            if key != "context"
        }
        sanitized["origin_context_bundle_id"] = str(
            claims["context_bundle_id"]
        )
        sanitized["context_bundle_id"] = clone_id
        sanitized["hub_research_dispatch_admission"] = {
            "schema": _ADMISSION_SCHEMA,
            "authority": "hub",
            "parent_task_id": str(claims["parent_task_id"]),
            "worker_job_id": str(claims["worker_job_id"]),
            "organization_assignment_id": str(
                claims["organization_assignment_id"]
            ),
            "origin_context_bundle_id": str(claims["context_bundle_id"]),
            "origin_context_bundle_digest": str(
                claims["source_context_bundle_digest"]
            ),
            "destination_binding_digest": str(
                claims["destination_binding_digest"]
            ),
            "payload_digest": str(claims["payload_digest"]),
        }
        return sanitized

    @staticmethod
    def _clone_metadata(
        metadata: Mapping[str, Any],
        *,
        claims: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            **dict(metadata),
            "hub_research_dispatch": {
                "schema": _DELEGATED_BUNDLE_SCHEMA,
                "authority": "hub",
                "parent_task_id": str(claims["parent_task_id"]),
                "worker_job_id": str(claims["worker_job_id"]),
                "organization_assignment_id": str(
                    claims["organization_assignment_id"]
                ),
                "origin_context_bundle_id": str(
                    claims["context_bundle_id"]
                ),
                "origin_context_bundle_digest": str(
                    claims["source_context_bundle_digest"]
                ),
                "destination_binding_digest": str(
                    claims["destination_binding_digest"]
                ),
                "payload_digest": str(claims["payload_digest"]),
            },
        }

    @classmethod
    def _is_matching_clone(
        cls,
        clone: ContextBundleDB,
        *,
        task_id: str,
        validated: Mapping[str, Any],
        payload_digest: str,
        claims: Mapping[str, Any],
    ) -> bool:
        dispatch = cls._mapping(
            dict(clone.bundle_metadata or {}).get("hub_research_dispatch")
        )
        return bool(
            str(clone.task_id or "") == task_id
            and str(clone.bundle_type or "") == "worker_execution_context"
            and str(clone.context_text or "")
            == str(validated["context_text"])
            and list(clone.chunks or []) == list(validated["chunks"])
            and int(clone.token_estimate or 0)
            == int(validated["token_estimate"])
            and str(dispatch.get("payload_digest") or "") == payload_digest
            and str(dispatch.get("origin_context_bundle_id") or "")
            == str(validated["origin_bundle_id"])
            and str(dispatch.get("origin_context_bundle_digest") or "")
            == str(validated["origin_bundle_digest"])
            and str(dispatch.get("parent_task_id") or "")
            == str(claims.get("parent_task_id") or "")
            and str(dispatch.get("worker_job_id") or "")
            == str(claims.get("worker_job_id") or "")
            and str(dispatch.get("organization_assignment_id") or "")
            == str(claims.get("organization_assignment_id") or "")
            and str(dispatch.get("destination_binding_digest") or "")
            == str(claims.get("destination_binding_digest") or "")
        )

    @classmethod
    def _is_matching_replay(
        cls,
        task: TaskDB,
        *,
        payload_digest: str,
        clone_id: str,
        claims: Mapping[str, Any],
    ) -> bool:
        admission = cls._mapping(
            dict(task.worker_execution_context or {}).get(
                "hub_research_dispatch_admission"
            )
        )
        return bool(
            str(task.task_kind or "") == "planning_research"
            and str(task.context_bundle_id or "") == clone_id
            and str(task.parent_task_id or "")
            == str(claims.get("parent_task_id") or "")
            and str(task.current_worker_job_id or "")
            == str(claims.get("worker_job_id") or "")
            and str(admission.get("payload_digest") or "")
            == payload_digest
            and str(admission.get("parent_task_id") or "")
            == str(claims.get("parent_task_id") or "")
            and str(admission.get("worker_job_id") or "")
            == str(claims.get("worker_job_id") or "")
            and str(admission.get("organization_assignment_id") or "")
            == str(claims.get("organization_assignment_id") or "")
            and str(admission.get("origin_context_bundle_id") or "")
            == str(claims.get("context_bundle_id") or "")
            and str(
                admission.get("origin_context_bundle_digest") or ""
            )
            == str(claims.get("source_context_bundle_digest") or "")
            and str(admission.get("destination_binding_digest") or "")
            == str(claims.get("destination_binding_digest") or "")
        )

    @staticmethod
    def _clone_bundle_id(
        *,
        task_id: str,
        origin_bundle_id: str,
        origin_bundle_digest: str,
    ) -> str:
        digest = hashlib.sha256(
            "\x00".join(
                (task_id, origin_bundle_id, origin_bundle_digest)
            ).encode("utf-8")
        ).hexdigest()[:32]
        return f"delegated-context-{digest}"

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    def _verifier(
        self,
    ) -> OrganizationResearchDispatchCapabilityVerifier:
        if self._capability_verifier is not None:
            return self._capability_verifier
        from worker.runtime.native_graph.authorization import (
            load_ed25519_verification_key_ring,
        )

        try:
            key_ring = load_ed25519_verification_key_ring()
        except ValueError as exc:
            raise OrganizationResearchWorkerIntakeError(
                "organization_research_dispatch_verification_keyring_invalid",
                status_code=503,
            ) from exc
        if key_ring is None:
            raise OrganizationResearchWorkerIntakeError(
                "organization_research_dispatch_verification_keyring_required",
                status_code=503,
            )
        self._capability_verifier = (
            OrganizationResearchDispatchCapabilityVerifier(key_ring)
        )
        return self._capability_verifier

    @staticmethod
    def _fail(reason_code: str, *, status_code: int = 403) -> None:
        raise OrganizationResearchWorkerIntakeError(
            reason_code,
            status_code=status_code,
        )


_SERVICE: OrganizationResearchWorkerIntakeService | None = None


def get_organization_research_worker_intake_service(
) -> OrganizationResearchWorkerIntakeService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = OrganizationResearchWorkerIntakeService()
    return _SERVICE


__all__ = [
    "OrganizationResearchWorkerIntakeError",
    "OrganizationResearchWorkerIntakeService",
    "get_organization_research_worker_intake_service",
    "organization_research_dispatch_payload_digest",
]
