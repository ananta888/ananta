"""Safe Hub-owned bulk planning/execution for source lifecycle mutations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal, Mapping, Protocol, Sequence


_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_MUTATIONS = frozenset({"refresh", "disable", "reindex", "grant_revoke"})


class SourceControlBulkError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class BulkTarget:
    resource_id: str
    expected_etag: str


@dataclass(frozen=True)
class BulkAuthorization:
    allowed: bool
    reason_code: str
    current_etag: str


class BulkAuthorizationPort(Protocol):
    def authorize(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        mutation: str,
        target: BulkTarget,
    ) -> BulkAuthorization: ...


class BulkMutationPort(Protocol):
    def execute(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        mutation: str,
        target: BulkTarget,
        idempotency_key: str,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class BulkTargetCheckpoint:
    target_ordinal: int
    resource_id: str
    target_digest: str
    state: Literal["executing", "completed"]
    result: Mapping[str, object] | None = None


@dataclass(frozen=True)
class BulkIdempotencyClaim:
    state: Literal["claimed", "completed", "in_progress"]
    result: Mapping[str, object] | None = None
    claim_token: str | None = None
    lease_expires_at_epoch: float | None = None
    checkpoints: tuple[BulkTargetCheckpoint, ...] = ()


class BulkIdempotencyPort(Protocol):
    """Lease-based durable operation and target journal.

    Mutation adapters must honor the deterministic per-target idempotency key.
    This makes an ``executing`` checkpoint safe to retry after a process crash.
    """

    def claim(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
    ) -> BulkIdempotencyClaim: ...

    def complete(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str,
        result: Mapping[str, object],
    ) -> None: ...

    def begin_target(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str,
        target_ordinal: int,
        resource_id: str,
        target_digest: str,
    ) -> BulkTargetCheckpoint: ...

    def complete_target(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        claim_token: str,
        target_ordinal: int,
        target_digest: str,
        result: Mapping[str, object],
    ) -> BulkTargetCheckpoint: ...


@dataclass(frozen=True)
class BulkPlanItem:
    resource_id: str
    expected_etag: str
    current_etag: str
    allowed: bool
    reason_code: str


@dataclass(frozen=True)
class BulkMutationPlan:
    schema: str
    tenant_id: str
    project_id: str
    actor_id: str
    mutation: str
    items: tuple[BulkPlanItem, ...]
    plan_digest: str


class SourceControlBulkService:
    def __init__(
        self,
        *,
        authorization: BulkAuthorizationPort,
        mutations: BulkMutationPort,
        idempotency: BulkIdempotencyPort,
        max_targets: int = 100,
    ) -> None:
        if max_targets < 1 or max_targets > 500:
            raise SourceControlBulkError("bulk_limit_invalid")
        self._authorization = authorization
        self._mutations = mutations
        self._idempotency = idempotency
        self._max_targets = max_targets

    def plan(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        mutation: str,
        targets: Sequence[BulkTarget],
        dry_run: bool,
    ) -> BulkMutationPlan:
        _validate_scope(tenant_id, project_id, actor_id)
        if mutation not in _MUTATIONS:
            raise SourceControlBulkError("bulk_mutation_invalid")
        if dry_run is not True:
            raise SourceControlBulkError("bulk_dry_run_required")
        if not targets or len(targets) > self._max_targets:
            raise SourceControlBulkError("bulk_target_count_invalid")
        if len({target.resource_id for target in targets}) != len(targets):
            raise SourceControlBulkError("bulk_duplicate_target")
        items: list[BulkPlanItem] = []
        for target in targets:
            _validate_target(target)
            auth = self._authorization.authorize(
                tenant_id=tenant_id,
                project_id=project_id,
                actor_id=actor_id,
                mutation=mutation,
                target=target,
            )
            items.append(
                BulkPlanItem(
                    resource_id=target.resource_id,
                    expected_etag=target.expected_etag,
                    current_etag=auth.current_etag,
                    allowed=auth.allowed
                    and auth.current_etag == target.expected_etag,
                    reason_code=(
                        auth.reason_code
                        if auth.current_etag == target.expected_etag
                        else "version_conflict"
                    ),
                )
            )
        payload = _plan_payload(
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            mutation=mutation,
            items=items,
        )
        return BulkMutationPlan(
            schema="ananta.source-control.bulk-plan.v1",
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            mutation=mutation,
            items=tuple(items),
            plan_digest=_digest(payload),
        )

    def execute(
        self,
        *,
        plan: BulkMutationPlan,
        supplied_plan_digest: str,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        if supplied_plan_digest != plan.plan_digest:
            raise SourceControlBulkError("bulk_plan_digest_mismatch")
        if not _OPAQUE_ID.fullmatch(str(idempotency_key or "")):
            raise SourceControlBulkError("idempotency_key_invalid")
        claim = self._idempotency.claim(
            idempotency_key=idempotency_key,
            plan_digest=plan.plan_digest,
        )
        if claim.state == "completed":
            if claim.result is None:
                raise SourceControlBulkError(
                    "bulk_idempotency_result_missing"
                )
            return claim.result
        if claim.state == "in_progress":
            raise SourceControlBulkError(
                "bulk_idempotency_in_progress"
            )
        if not claim.claim_token:
            raise SourceControlBulkError("bulk_claim_token_missing")
        checkpoints = {
            checkpoint.target_ordinal: checkpoint
            for checkpoint in claim.checkpoints
        }
        results: list[dict[str, object]] = []
        for ordinal, item in enumerate(plan.items):
            target_digest = _digest(
                {
                    "plan_digest": plan.plan_digest,
                    "target_ordinal": ordinal,
                    "resource_id": item.resource_id,
                    "expected_etag": item.expected_etag,
                    "current_etag": item.current_etag,
                }
            )
            checkpoint = checkpoints.get(ordinal)
            if checkpoint is not None:
                if (
                    checkpoint.resource_id != item.resource_id
                    or checkpoint.target_digest != target_digest
                ):
                    raise SourceControlBulkError(
                        "bulk_target_checkpoint_mismatch"
                    )
                if checkpoint.state == "completed":
                    if checkpoint.result is None:
                        raise SourceControlBulkError(
                            "bulk_target_result_missing"
                        )
                    results.append(dict(checkpoint.result))
                    continue
            target = BulkTarget(
                resource_id=item.resource_id,
                expected_etag=item.current_etag,
            )
            if checkpoint is None:
                current = self._authorization.authorize(
                    tenant_id=plan.tenant_id,
                    project_id=plan.project_id,
                    actor_id=plan.actor_id,
                    mutation=plan.mutation,
                    target=target,
                )
                self._idempotency.begin_target(
                    idempotency_key=idempotency_key,
                    plan_digest=plan.plan_digest,
                    claim_token=claim.claim_token,
                    target_ordinal=ordinal,
                    resource_id=item.resource_id,
                    target_digest=target_digest,
                )
                if (
                    not item.allowed
                    or not current.allowed
                    or current.current_etag != item.current_etag
                ):
                    skipped = {
                        "resource_id": item.resource_id,
                        "status": (
                            "skipped" if not item.allowed else "failed"
                        ),
                        "reason_code": (
                            item.reason_code
                            if not item.allowed
                            else (
                                current.reason_code
                                if not current.allowed
                                else "version_conflict"
                            )
                        ),
                    }
                    self._idempotency.complete_target(
                        idempotency_key=idempotency_key,
                        plan_digest=plan.plan_digest,
                        claim_token=claim.claim_token,
                        target_ordinal=ordinal,
                        target_digest=target_digest,
                        result=skipped,
                    )
                    results.append(skipped)
                    continue
            try:
                result = dict(
                    self._mutations.execute(
                        tenant_id=plan.tenant_id,
                        project_id=plan.project_id,
                        actor_id=plan.actor_id,
                        mutation=plan.mutation,
                        target=target,
                        idempotency_key=(
                            f"{idempotency_key}:{_digest(item.resource_id)[:16]}"
                        ),
                    )
                )
                result.setdefault("resource_id", item.resource_id)
                result.setdefault("status", "accepted")
            except Exception:
                result = {
                    "resource_id": item.resource_id,
                    "status": "failed",
                    "reason_code": "mutation_failed",
                }
            self._idempotency.complete_target(
                idempotency_key=idempotency_key,
                plan_digest=plan.plan_digest,
                claim_token=claim.claim_token,
                target_ordinal=ordinal,
                target_digest=target_digest,
                result=result,
            )
            results.append(result)
        response: Mapping[str, object] = {
            "schema": "ananta.source-control.bulk-result.v1",
            "plan_digest": plan.plan_digest,
            "results": results,
        }
        self._idempotency.complete(
            idempotency_key=idempotency_key,
            plan_digest=plan.plan_digest,
            claim_token=claim.claim_token,
            result=response,
        )
        return response


def _validate_scope(tenant_id: str, project_id: str, actor_id: str) -> None:
    for name, value in (
        ("tenant_id", tenant_id),
        ("project_id", project_id),
        ("actor_id", actor_id),
    ):
        if not _OPAQUE_ID.fullmatch(str(value or "")):
            raise SourceControlBulkError(f"{name}_invalid")


def _validate_target(target: BulkTarget) -> None:
    if not _OPAQUE_ID.fullmatch(str(target.resource_id or "")):
        raise SourceControlBulkError("bulk_resource_id_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", str(target.expected_etag or "")):
        raise SourceControlBulkError("bulk_expected_etag_invalid")


def _plan_payload(
    *,
    tenant_id: str,
    project_id: str,
    actor_id: str,
    mutation: str,
    items: Sequence[BulkPlanItem],
) -> dict[str, object]:
    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "actor_id": actor_id,
        "mutation": mutation,
        "items": [
            {
                "resource_id": item.resource_id,
                "expected_etag": item.expected_etag,
                "current_etag": item.current_etag,
                "allowed": item.allowed,
                "reason_code": item.reason_code,
            }
            for item in items
        ],
    }


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
