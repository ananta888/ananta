"""Effective access and Context Policy lifecycle facade of the Source Control API runtime.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.services.context_policy_lifecycle import ContextPolicyActor
from agent.services.effective_source_access_service import (
    EffectiveSourceAccessService,
)
from agent.services.source_control_projection_service import (
    SourceControlPrincipal,
)
from agent.services.source_control_runtime_support import (
    SourceControlApiRuntimeError,
    decode_cursor,
    encode_cursor,
    runtime_principal,
    wire,
)
from ananta_contracts.source_control import GrantOperation, GrantTransformation


class SourceControlPolicyAccessApi:
    """Effective access previews/matrices and Context Policy lifecycle calls."""

    def __init__(
        self,
        *,
        engine: Engine,
        access: object | None,
        context_policy: object | None,
    ) -> None:
        self._engine = engine
        self._access = access
        self._policy_lifecycle = context_policy

    def access_preview(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        access = self._effective_access(actor)
        return wire(
            access.preview(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                source_revision_id=str(payload["source_revision_id"]),
                destination_id=str(payload["destination_id"]),
                operation=GrantOperation(str(payload["operation"])),
                transformation=GrantTransformation(
                    str(payload["transformation"])
                ),
                purpose=str(payload["purpose"]),
            )
        )

    def access_matrix(
        self, *, principal: object, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        access = self._effective_access(actor)
        value = wire(
            access.matrix(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
                operation=GrantOperation(str(payload["operation"])),
                transformation=GrantTransformation(
                    str(payload["transformation"])
                ),
                purpose=str(payload["purpose"]),
                source_cursor=payload.get("source_cursor"),
                destination_cursor=payload.get("destination_cursor"),
                source_limit=int(payload.get("source_limit", 25)),
                destination_limit=int(payload.get("destination_limit", 25)),
                source_filters=payload.get("source_filters") or {},
                destination_filters=payload.get("destination_filters") or {},
            )
        )
        return {
            "items": value.get("items", value.get("rows", [])),
            "source_next_cursor": value.get(
                "source_next_cursor",
                value.get("next_source_cursor"),
            ),
            "destination_next_cursor": value.get(
                "destination_next_cursor",
                value.get("next_destination_cursor"),
            ),
        }

    def context_policy_list(
        self, *, principal: object, cursor: str | None, limit: int
    ) -> Mapping[str, object]:
        actor = runtime_principal(principal)
        after = decode_cursor(cursor)
        with Session(self._engine) as db:
            rows = list(
                db.exec(
                    select(ContextPolicyVersionDB)
                    .where(
                        ContextPolicyVersionDB.tenant_id == actor.tenant_id,
                        ContextPolicyVersionDB.project_id == actor.project_id,
                    )
                    .order_by(
                        ContextPolicyVersionDB.policy_id,
                        ContextPolicyVersionDB.version.desc(),
                    )
                ).all()
            )
        latest: dict[str, ContextPolicyVersionDB] = {}
        for row in rows:
            if after is not None and row.policy_id <= after:
                continue
            latest.setdefault(row.policy_id, row)
        selected = list(latest.values())[: limit + 1]
        visible = selected[:limit]
        return {
            "items": [
                {
                    "policy_id": row.policy_id,
                    "latest_version": row.version,
                    "state": row.state,
                    "etag": row.etag,
                    "policy_digest": row.policy_digest,
                }
                for row in visible
            ],
            "next_cursor": (
                encode_cursor(visible[-1].policy_id)
                if len(selected) > limit and visible
                else None
            ),
        }

    def context_policy_versions(
        self,
        *,
        principal: object,
        policy_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, object]:
        service = self._context_policy()
        items, next_cursor = service.versions(
            actor=_context_actor(principal),
            policy_id=policy_id,
            cursor=cursor,
            limit=limit,
        )
        return {
            "items": wire(items),
            "next_cursor": next_cursor,
        }

    def context_policy_detail(
        self, *, principal: object, policy_id: str, version: int
    ) -> tuple[Mapping[str, object], str]:
        item = self._context_policy().detail(
            actor=_context_actor(principal),
            policy_id=policy_id,
            version=version,
        )
        return wire(item), str(item.etag)

    def context_policy_active(
        self, *, principal: object, policy_id: str
    ) -> tuple[Mapping[str, object], str]:
        item = self._context_policy().active(
            actor=_context_actor(principal),
            policy_id=policy_id,
        )
        return wire(item), str(item.etag)

    def context_policy_draft(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        item = self._context_policy().create_draft(
            actor=_context_actor(principal),
            policy_id=policy_id,
            document=dict(payload["document"]),
            expected_latest_version=payload.get("expected_latest_version"),
            idempotency_key=idempotency_key,
        )
        return wire(item)

    def context_policy_lint(
        self, *, principal: object, policy_id: str, version: int
    ) -> Mapping[str, object]:
        diagnostics = self._context_policy().lint(
            actor=_context_actor(principal),
            policy_id=policy_id,
            version=version,
        )
        return {"diagnostics": wire(diagnostics)}

    def context_policy_preview(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        preview = self._context_policy().preview(
            actor=_context_actor(principal),
            policy_id=policy_id,
            version=int(payload["version"]),
            source_revision_id=str(payload["source_revision_id"]),
            destination_id=str(payload["destination_id"]),
            operation=GrantOperation(str(payload["operation"])),
            transformation=GrantTransformation(
                str(payload["transformation"])
            ),
        )
        return wire(preview)

    def context_policy_transition(
        self,
        *,
        principal: object,
        operation: str,
        policy_id: str,
        version: int,
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        service = self._context_policy()
        method = (
            service.activate if operation == "activate" else service.revoke
        )
        return wire(
            method(
                actor=_context_actor(principal),
                policy_id=policy_id,
                version=version,
                if_match=if_match,
                idempotency_key=idempotency_key,
            )
        )

    def context_policy_rollback(
        self,
        *,
        principal: object,
        policy_id: str,
        payload: Mapping[str, object],
        if_match: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        service = self._context_policy()
        versions, _ = service.versions(
            actor=_context_actor(principal),
            policy_id=policy_id,
            limit=1,
        )
        if not versions or versions[0].etag != if_match.strip('"'):
            raise SourceControlApiRuntimeError(
                "policy_version_conflict", status_code=412
            )
        return wire(
            service.rollback(
                actor=_context_actor(principal),
                policy_id=policy_id,
                target_version=int(payload["target_version"]),
                expected_latest_version=int(
                    payload["expected_latest_version"]
                ),
                idempotency_key=idempotency_key,
            )
        )

    def _context_policy(self):
        if self._policy_lifecycle is None:
            raise SourceControlApiRuntimeError(
                "context_policy_lifecycle_unavailable", status_code=503
            )
        return self._policy_lifecycle

    def _effective_access(
        self, actor: SourceControlPrincipal
    ) -> EffectiveSourceAccessService:
        if self._access is None:
            raise SourceControlApiRuntimeError(
                "effective_source_access_unavailable", status_code=503
            )
        if callable(self._access):
            service = self._access(
                tenant_id=actor.tenant_id,
                project_id=actor.project_id,
            )
        else:
            service = self._access
        if not isinstance(service, EffectiveSourceAccessService):
            raise SourceControlApiRuntimeError(
                "effective_source_access_invalid", status_code=500
            )
        return service


def _context_actor(value: object) -> ContextPolicyActor:
    actor = runtime_principal(value)
    return ContextPolicyActor(
        subject_id=actor.subject_id,
        tenant_id=actor.tenant_id,
        project_id=actor.project_id,
        roles=actor.roles,
    )


