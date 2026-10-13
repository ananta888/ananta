"""Current authoritative Hub verification for pinned production release runs."""

from collections.abc import Iterable
from dataclasses import dataclass

from agent.ports.evidence_identity import RunEvidenceIdentity
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryError, HubEvidenceRegistryService
from agent.services.ml_intern_provenance_contract import (
    MlInternTrainingContractError,
    normalize_run_ids,
    normalize_source_ids,
)
from agent.services.unsloth_evidence import EvidenceReferences, EvidenceVerificationError


@dataclass(frozen=True)
class ReleaseExecutionBinding:
    task_id: str
    assignment_id: str
    dispatch_lease_id: str
    input_digest: str
    result_digest: str


class HubRegisteredReleaseEvidence:
    """Resolve existing pinned identities only; never issue or promote evidence."""

    def __init__(self, *, registry: HubEvidenceRegistryService, runs: Iterable[RunEvidenceIdentity]):
        self._registry = registry
        items = tuple(runs)
        self._runs = {run.run_id: run for run in items}
        if not items or len(items) != len(self._runs):
            raise ValueError("release_evidence_run_pins_invalid")
        if len({(run.tenant_id, run.project_id) for run in items}) != 1:
            raise ValueError("release_evidence_scope_pins_invalid")

    def require_scope(self, *, tenant_id: str) -> None:
        if any(run.tenant_id != tenant_id for run in self._runs.values()):
            raise EvidenceVerificationError("release_evidence_tenant_mismatch", "release evidence has another tenant")

    def require_execution_bindings(self, bindings: Iterable[ReleaseExecutionBinding]) -> None:
        actual = tuple(bindings)
        expected = tuple(ReleaseExecutionBinding(
            pin.task_id, pin.assignment_id, pin.dispatch_lease_id, pin.input_digest, str(pin.result_digest or ""),
        ) for pin in self._runs.values())
        if len(set(actual)) != len(actual) or len(actual) != len(expected) or set(actual) != set(expected):
            raise EvidenceVerificationError(
                "release_evidence_execution_mismatch", "Release runs do not bind the current Hub executions",
            )
        self.resolve(
            source_ids={source for pin in self._runs.values() for source in pin.source_ids},
            run_ids=tuple(self._runs),
        )

    def resolve(self, *, source_ids: Iterable[str], run_ids: Iterable[str]) -> EvidenceReferences:
        try:
            sources = tuple(sorted(normalize_source_ids(list(source_ids))))
            runs = tuple(sorted(normalize_run_ids(list(run_ids))))
        except MlInternTrainingContractError as exc:
            raise EvidenceVerificationError(exc.reason_code, str(exc)) from exc
        if not sources or not runs or set(runs) != set(self._runs):
            raise EvidenceVerificationError("release_evidence_pins_mismatch", "release evidence is not pinned")
        if set(sources) != {source for run in self._runs.values() for source in run.source_ids}:
            raise EvidenceVerificationError("release_evidence_sources_mismatch", "release sources are not pinned")
        for run_id in runs:
            pin = self._runs[run_id]
            try:
                self._registry.require_run_result(
                    tenant_id=pin.tenant_id, project_id=pin.project_id, run_id=pin.run_id,
                    task_id=pin.task_id, assignment_id=pin.assignment_id, dispatch_lease_id=pin.dispatch_lease_id,
                    input_digest=pin.input_digest, source_ids=pin.source_ids,
                    result_digest=str(pin.result_digest or ""), expected_binding_digest=pin.binding_digest,
                )
            except HubEvidenceRegistryError as exc:
                raise EvidenceVerificationError(exc.reason_code, str(exc)) from exc
            verified = self._registry.verify_release_binding(
                tenant_id=pin.tenant_id, project_id=pin.project_id, run_id=pin.run_id,
                required_scope="production", task_id=pin.task_id,
                repository_revision=pin.repository_revision, source_ids=pin.source_ids,
            )
            if not verified.verified:
                raise EvidenceVerificationError(verified.reason_code, "Hub production release binding refused")
        return EvidenceReferences(source_ids=sources, run_ids=runs)
