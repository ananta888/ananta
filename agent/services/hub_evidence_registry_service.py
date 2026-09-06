"""Hub-only automatic issuance and verification of evidence identities."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Sequence
from typing import Any, Literal

from agent.ports.evidence_identity import (
    EvidenceBindingVerification,
    EvidenceIdentityRepositoryPort,
    RunEvidenceIdentity,
    SourceEvidenceIdentity,
)

EvidenceScope = Literal["test", "local", "external", "production"]
_SCOPES = frozenset({"test", "local", "external", "production"})
_SOURCE_ID = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9_.:-]{0,187}$")
_RUN_ID = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9_.:-]{0,187}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
_HUB_ISSUER = "hub-evidence-registry"
_SOURCE_SCOPES_BY_RUN = {
    "test": frozenset({"test", "local", "external", "production"}),
    "local": frozenset({"local", "external", "production"}),
    "external": frozenset({"external", "production"}),
    "production": frozenset({"external", "production"}),
}


class HubEvidenceRegistryError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _canonical_digest(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HubEvidenceRegistryError("evidence_binding_not_json") from exc
    return hashlib.sha256(payload).hexdigest()


class HubEvidenceRegistryService:
    """Issue immutable ``SRC_*``/``RUN_*`` authority in the Hub.

    Callers provide source facts and run intent, never an automatically minted
    identifier. Externally issued identifiers remain an explicit compatibility
    path and must name their non-Hub issuer. Workers only receive the persisted
    run projection after reservation and cannot access this service in their
    isolated container/database.
    """

    def __init__(
        self,
        repository: EvidenceIdentityRepositoryPort,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._clock = clock

    def register_source(
        self,
        *,
        tenant_id: str,
        project_id: str,
        origin_type: str,
        origin_digest: str,
        content_digest: str,
        policy_digest: str,
        evidence_scope: EvidenceScope,
        synthetic: bool = False,
        supplied_source_id: str | None = None,
        issuer: str = _HUB_ISSUER,
    ) -> SourceEvidenceIdentity:
        scope = self._scope(evidence_scope)
        if synthetic and scope in {"external", "production"}:
            raise HubEvidenceRegistryError("evidence_source_synthetic_scope_forbidden")
        binding = {
            "schema": "ananta.hub-source-evidence-binding.v1",
            "tenant_id": self._text(tenant_id, "evidence_tenant_required"),
            "project_id": self._text(project_id, "evidence_project_required"),
            "origin_type": self._text(origin_type, "evidence_source_origin_type_required", maximum=64),
            "origin_digest": self._digest(origin_digest, "evidence_source_origin_digest_invalid"),
            "content_digest": self._digest(content_digest, "evidence_source_content_digest_invalid"),
            "policy_digest": self._digest(policy_digest, "evidence_source_policy_digest_invalid"),
            "evidence_scope": scope,
            "synthetic": bool(synthetic),
            "issuer": self._issuer(issuer, supplied=bool(str(supplied_source_id or "").strip())),
        }
        binding_digest = _canonical_digest(binding)
        source_id = self._identifier(
            supplied_source_id,
            pattern=_SOURCE_ID,
            generated=f"SRC_{binding_digest[:32]}",
            reason="evidence_source_identifier_invalid",
        )
        identity = SourceEvidenceIdentity(
            source_id=source_id,
            tenant_id=binding["tenant_id"],
            project_id=binding["project_id"],
            origin_type=binding["origin_type"],
            origin_digest=binding["origin_digest"],
            content_digest=binding["content_digest"],
            policy_digest=binding["policy_digest"],
            evidence_scope=scope,
            synthetic=bool(synthetic),
            issuer=binding["issuer"],
            state="admitted",
            binding_digest=binding_digest,
            created_at_epoch=float(self._clock()),
        )
        return self._repository.register_source(identity)

    def require_source_identity(
        self,
        *,
        tenant_id: str,
        project_id: str,
        source_id: str,
        expected_binding_digest: str,
    ) -> SourceEvidenceIdentity:
        """Verify an already pinned immutable source, without issuing or promoting it.

        Consumers must separately check evidence scope and domain permissions.
        A registered license document, for example, is not a publication grant.
        """
        expected = self._digest(expected_binding_digest, "evidence_source_binding_digest_invalid")
        if not isinstance(source_id, str) or _SOURCE_ID.fullmatch(source_id) is None:
            raise HubEvidenceRegistryError("evidence_source_identifier_invalid")
        source = self._repository.get_source(tenant_id=tenant_id, project_id=project_id, source_id=source_id)
        if source is None or source.state != "admitted":
            raise HubEvidenceRegistryError("evidence_source_identity_unavailable")
        binding = {
            "schema": "ananta.hub-source-evidence-binding.v1",
            **{
                name: getattr(source, name)
                for name in (
                    "tenant_id",
                    "project_id",
                    "origin_type",
                    "origin_digest",
                    "content_digest",
                    "policy_digest",
                    "evidence_scope",
                    "synthetic",
                    "issuer",
                )
            },
        }
        if (
            (source.tenant_id, source.project_id, source.source_id) != (tenant_id, project_id, source_id)
            or source.binding_digest != expected
            or _canonical_digest(binding) != expected
            or (source.issuer == _HUB_ISSUER and source_id != f"SRC_{expected[:32]}")
        ):
            raise HubEvidenceRegistryError("evidence_source_identity_mutated")
        return source

    def reserve_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        task_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
        repository_revision: str,
        input_digest: str,
        execution_profile_digest: str,
        environment_digest: str,
        source_ids: Sequence[str],
        evidence_scope: EvidenceScope,
        idempotency_key: str,
        synthetic: bool = False,
        supplied_run_id: str | None = None,
        issuer: str = _HUB_ISSUER,
    ) -> RunEvidenceIdentity:
        scope = self._scope(evidence_scope)
        if synthetic and scope in {"external", "production"}:
            raise HubEvidenceRegistryError("evidence_run_synthetic_scope_forbidden")
        tenant = self._text(tenant_id, "evidence_tenant_required")
        project = self._text(project_id, "evidence_project_required")
        sources = self._source_ids(source_ids)
        for source_id in sources:
            source = self._repository.get_source(
                tenant_id=tenant,
                project_id=project,
                source_id=source_id,
            )
            if source is None or source.state != "admitted":
                raise HubEvidenceRegistryError("evidence_run_source_identity_unavailable")
            if source.evidence_scope not in _SOURCE_SCOPES_BY_RUN[scope]:
                raise HubEvidenceRegistryError("evidence_run_source_scope_forbidden")
            if source.synthetic and scope != "test":
                raise HubEvidenceRegistryError("evidence_run_synthetic_source_forbidden")
        key = self._text(
            idempotency_key,
            "evidence_run_idempotency_key_invalid",
            minimum=8,
            maximum=191,
        )
        if any(character.isspace() for character in key):
            raise HubEvidenceRegistryError("evidence_run_idempotency_key_invalid")
        revision = str(repository_revision or "").strip().lower()
        if _REVISION.fullmatch(revision) is None:
            raise HubEvidenceRegistryError("evidence_run_repository_revision_invalid")
        issuer_value = self._issuer(issuer, supplied=bool(str(supplied_run_id or "").strip()))
        reservation_key_digest = _canonical_digest(
            {
                "schema": "ananta.hub-run-evidence-reservation.v1",
                "tenant_id": tenant,
                "project_id": project,
                "idempotency_key": key,
            }
        )
        binding = {
            "schema": "ananta.hub-run-evidence-binding.v1",
            "tenant_id": tenant,
            "project_id": project,
            "task_id": self._text(task_id, "evidence_run_task_required"),
            "assignment_id": self._text(assignment_id, "evidence_run_assignment_required"),
            "dispatch_lease_id": self._text(dispatch_lease_id, "evidence_run_dispatch_lease_required"),
            "repository_revision": revision,
            "input_digest": self._digest(input_digest, "evidence_run_input_digest_invalid"),
            "execution_profile_digest": self._digest(
                execution_profile_digest,
                "evidence_run_execution_profile_digest_invalid",
            ),
            "environment_digest": self._digest(environment_digest, "evidence_run_environment_digest_invalid"),
            "source_ids": list(sources),
            "evidence_scope": scope,
            "synthetic": bool(synthetic),
            "issuer": issuer_value,
            "reservation_key_digest": reservation_key_digest,
        }
        binding_digest = _canonical_digest(binding)
        run_id = self._identifier(
            supplied_run_id,
            pattern=_RUN_ID,
            generated=f"RUN_{binding_digest[:32]}",
            reason="evidence_run_identifier_invalid",
        )
        now = float(self._clock())
        identity = RunEvidenceIdentity(
            run_id=run_id,
            tenant_id=tenant,
            project_id=project,
            task_id=binding["task_id"],
            assignment_id=binding["assignment_id"],
            dispatch_lease_id=binding["dispatch_lease_id"],
            repository_revision=revision,
            input_digest=binding["input_digest"],
            execution_profile_digest=binding["execution_profile_digest"],
            environment_digest=binding["environment_digest"],
            source_ids=sources,
            evidence_scope=scope,
            synthetic=bool(synthetic),
            issuer=issuer_value,
            reservation_key_digest=reservation_key_digest,
            binding_digest=binding_digest,
            state="reserved",
            result_digest=None,
            created_at_epoch=now,
            updated_at_epoch=now,
        )
        return self._repository.reserve_run(identity)

    def record_result(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
        terminal_state: Literal["succeeded", "failed", "cancelled"],
        result_digest: str,
    ) -> RunEvidenceIdentity:
        if terminal_state not in _TERMINAL_STATES:
            raise HubEvidenceRegistryError("evidence_run_terminal_state_invalid")
        return self._repository.complete_run(
            tenant_id=self._text(tenant_id, "evidence_tenant_required"),
            project_id=self._text(project_id, "evidence_project_required"),
            run_id=self._identifier(
                run_id,
                pattern=_RUN_ID,
                generated="",
                reason="evidence_run_identifier_invalid",
            ),
            assignment_id=self._text(assignment_id, "evidence_run_assignment_required"),
            dispatch_lease_id=self._text(dispatch_lease_id, "evidence_run_dispatch_lease_required"),
            terminal_state=terminal_state,
            result_digest=self._digest(result_digest, "evidence_run_result_digest_invalid"),
            updated_at_epoch=float(self._clock()),
        )

    def assignment_projection(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        task_id: str,
        assignment_id: str,
        dispatch_lease_id: str,
    ) -> dict[str, Any]:
        """Return only the closed authority a delegated Worker may consume."""

        from ananta_contracts.hub_evidence import build_hub_evidence_assignment

        run = self._repository.get_run(
            tenant_id=str(tenant_id or "").strip(),
            project_id=str(project_id or "").strip(),
            run_id=str(run_id or "").strip(),
        )
        if run is None or run.state != "reserved":
            raise HubEvidenceRegistryError("evidence_run_reservation_unavailable")
        if (
            run.task_id != str(task_id or "").strip()
            or run.assignment_id != str(assignment_id or "").strip()
            or run.dispatch_lease_id != str(dispatch_lease_id or "").strip()
        ):
            raise HubEvidenceRegistryError("evidence_run_assignment_binding_mismatch")
        return build_hub_evidence_assignment(
            run_id=run.run_id,
            task_id=run.task_id,
            assignment_id=run.assignment_id,
            dispatch_lease_id=run.dispatch_lease_id,
            source_ids=run.source_ids,
            evidence_scope=run.evidence_scope,
            binding_digest=run.binding_digest,
        )

    def verify_release_binding(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        required_scope: Literal["local", "external", "production"],
        task_id: str,
        repository_revision: str,
        source_ids: Sequence[str],
    ) -> EvidenceBindingVerification:
        raw_sources = tuple(sorted(str(value or "").strip() for value in source_ids))
        try:
            requested_sources = self._source_ids(source_ids)
        except HubEvidenceRegistryError:
            return self._failed("evidence_source_ids_invalid", raw_sources, run_id)
        normalized_required_scope = str(required_scope or "").strip().lower()
        if normalized_required_scope not in {"local", "external", "production"}:
            return self._failed("evidence_release_scope_invalid", requested_sources, run_id)
        run = self._repository.get_run(
            tenant_id=str(tenant_id or "").strip(),
            project_id=str(project_id or "").strip(),
            run_id=str(run_id or "").strip(),
        )
        if run is None:
            return self._failed("evidence_run_identity_not_found", requested_sources, run_id)
        if run.state != "succeeded" or not run.result_digest:
            return self._failed("evidence_run_not_successful", requested_sources, run_id, run)
        if (
            run.task_id != str(task_id or "").strip()
            or run.repository_revision != str(repository_revision or "").strip().lower()
            or run.source_ids != requested_sources
        ):
            return self._failed("evidence_run_binding_mismatch", requested_sources, run_id, run)
        if run.synthetic or run.evidence_scope == "test":
            return self._failed("evidence_run_test_scope_forbidden", requested_sources, run_id, run)
        if run.evidence_scope != normalized_required_scope:
            return self._failed("evidence_run_scope_mismatch", requested_sources, run_id, run)
        for source_id in requested_sources:
            source = self._repository.get_source(
                tenant_id=run.tenant_id,
                project_id=run.project_id,
                source_id=source_id,
            )
            if (
                source is None
                or source.state != "admitted"
                or source.synthetic
                or source.evidence_scope not in _SOURCE_SCOPES_BY_RUN[normalized_required_scope]
            ):
                return self._failed(
                    "evidence_source_release_binding_invalid",
                    requested_sources,
                    run_id,
                    run,
                )
        return EvidenceBindingVerification(
            verified=True,
            reason_code="verified",
            source_ids=requested_sources,
            run_id=run.run_id,
            evidence_scope=run.evidence_scope,
        )

    @staticmethod
    def _failed(
        reason: str,
        source_ids: tuple[str, ...],
        run_id: str,
        run: RunEvidenceIdentity | None = None,
    ) -> EvidenceBindingVerification:
        return EvidenceBindingVerification(
            verified=False,
            reason_code=reason,
            source_ids=source_ids,
            run_id=str(run_id or "").strip(),
            evidence_scope=run.evidence_scope if run is not None else None,
        )

    @staticmethod
    def _scope(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _SCOPES:
            raise HubEvidenceRegistryError("evidence_scope_invalid")
        return normalized

    @staticmethod
    def _digest(value: str, reason: str) -> str:
        normalized = str(value or "").strip().lower().removeprefix("sha256:")
        if _DIGEST.fullmatch(normalized) is None:
            raise HubEvidenceRegistryError(reason)
        return normalized

    @staticmethod
    def _text(
        value: str,
        reason: str,
        *,
        minimum: int = 1,
        maximum: int = 191,
    ) -> str:
        normalized = str(value or "").strip()
        if not minimum <= len(normalized) <= maximum:
            raise HubEvidenceRegistryError(reason)
        return normalized

    @staticmethod
    def _issuer(value: str, *, supplied: bool) -> str:
        normalized = str(value or "").strip()
        if not normalized or len(normalized) > 128:
            raise HubEvidenceRegistryError("evidence_issuer_invalid")
        if supplied and normalized == _HUB_ISSUER:
            raise HubEvidenceRegistryError("evidence_external_identifier_issuer_required")
        if not supplied and normalized != _HUB_ISSUER:
            raise HubEvidenceRegistryError("evidence_automatic_identifier_issuer_invalid")
        return normalized

    @staticmethod
    def _identifier(
        supplied: str | None,
        *,
        pattern: re.Pattern[str],
        generated: str,
        reason: str,
    ) -> str:
        normalized = str(supplied or "").strip() or generated
        if pattern.fullmatch(normalized) is None:
            raise HubEvidenceRegistryError(reason)
        return normalized

    @staticmethod
    def _source_ids(values: Sequence[str]) -> tuple[str, ...]:
        if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 128:
            raise HubEvidenceRegistryError("evidence_source_ids_invalid")
        normalized = tuple(sorted(str(value or "").strip() for value in values))
        if len(set(normalized)) != len(normalized) or any(_SOURCE_ID.fullmatch(value) is None for value in normalized):
            raise HubEvidenceRegistryError("evidence_source_ids_invalid")
        return normalized


_SERVICE: HubEvidenceRegistryService | None = None


def get_hub_evidence_registry_service() -> HubEvidenceRegistryService:
    from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository

    global _SERVICE
    if _SERVICE is None:
        _SERVICE = HubEvidenceRegistryService(SqlEvidenceIdentityRepository())
    return _SERVICE


__all__ = [
    "EvidenceBindingVerification",
    "EvidenceScope",
    "HubEvidenceRegistryError",
    "HubEvidenceRegistryService",
    "get_hub_evidence_registry_service",
]
