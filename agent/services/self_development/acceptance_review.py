"""Publication requires a separately assigned, Registry-bound acceptance review."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from pydantic import TypeAdapter

from agent.ports.evidence_identity import RunEvidenceIdentity
from agent.services.hub_evidence_gate_service import canonical_evidence_digest
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService


class AcceptanceReviewError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class AcceptanceReviewRequest:
    task_id: str
    repository_revision: str
    author_worker_url: str
    requirements_json: str
    changed_files: tuple[tuple[str, str], ...]

    @property
    def digest(self) -> str:
        return canonical_evidence_digest(asdict(self))


@dataclass(frozen=True, slots=True)
class AcceptanceReviewResult:
    approved: bool
    criteria_passed: tuple[bool, ...]
    findings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcceptanceReviewReceipt:
    run: RunEvidenceIdentity
    worker_url: str
    result: AcceptanceReviewResult


class AcceptanceReviewer(Protocol):
    """Hub workflow adapter; dispatch a separate role Task, never self-review.

    Implementations must use the existing Hub task queue and reserve a run
    before delegation. Returning a receipt does not grant publication rights.
    """

    def review(self, request: AcceptanceReviewRequest) -> AcceptanceReviewReceipt: ...


def require_acceptance_review(
    request: AcceptanceReviewRequest,
    *,
    criteria_count: int,
    reviewer: AcceptanceReviewer | None,
    registry: HubEvidenceRegistryService,
    tenant_id: str,
) -> AcceptanceReviewReceipt:
    if reviewer is None:
        raise AcceptanceReviewError('independent_acceptance_reviewer_unavailable')
    receipt = reviewer.review(request)
    verify_acceptance_review(request, receipt, criteria_count=criteria_count,
                             registry=registry, tenant_id=tenant_id)
    return receipt


def verify_acceptance_review(
    request: AcceptanceReviewRequest,
    receipt: AcceptanceReviewReceipt,
    *,
    criteria_count: int,
    registry: HubEvidenceRegistryService,
    tenant_id: str,
) -> None:
    """Revalidate stored review authority before each publication operation."""
    run = receipt.run
    if (not receipt.worker_url
            or receipt.worker_url.rstrip('/') == request.author_worker_url.rstrip('/')
            or run.task_id == request.task_id):
        raise AcceptanceReviewError('independent_acceptance_review_self_review')
    result = receipt.result
    if (result.approved is not True or criteria_count < 1
            or len(result.criteria_passed) != criteria_count
            or not all(value is True for value in result.criteria_passed)
            or result.findings):
        raise AcceptanceReviewError('acceptance_criteria_not_satisfied')
    registered = registry.require_run_result(
        tenant_id=tenant_id, project_id='ananta-self-development', run_id=run.run_id,
        task_id=run.task_id, assignment_id=run.assignment_id,
        dispatch_lease_id=run.dispatch_lease_id, input_digest=request.digest,
        source_ids=run.source_ids, result_digest=canonical_evidence_digest(asdict(result)),
        expected_binding_digest=run.binding_digest)
    expected_environment = canonical_evidence_digest(
        {'worker': receipt.worker_url, 'phase': 'acceptance-review'})
    if (registered.repository_revision != request.repository_revision
            or registered.environment_digest != expected_environment):
        raise AcceptanceReviewError('acceptance_review_snapshot_mismatch')
    verification = registry.verify_release_binding(
        tenant_id=tenant_id, project_id='ananta-self-development', run_id=run.run_id,
        required_scope='local', task_id=run.task_id,
        repository_revision=request.repository_revision, source_ids=run.source_ids)
    if not verification.verified:
        raise AcceptanceReviewError('acceptance_review_evidence_unverified')


review_receipt_adapter = TypeAdapter(AcceptanceReviewReceipt)
review_request_adapter = TypeAdapter(AcceptanceReviewRequest)
