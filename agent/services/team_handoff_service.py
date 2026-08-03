"""Hub-owned cross-team handoffs over the existing artifact authority."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, Iterable, Mapping

from agent.ports.artifact_handoff import ArtifactVersionReader, EvidenceManifestVerifier, HandoffStateStore
from agent.services.separation_of_duties_service import (
    DutyAssignment,
    SeparationOfDutiesPolicy,
    SeparationOfDutiesService,
)


@dataclass(frozen=True, slots=True)
class TeamHandoffArtifactRef:
    artifact_id: str
    version: str
    digest: str
    artifact_kind: str = "artifact"
    artifact_version_ref: str = ""
    verification_status: str = "hub_verified"


@dataclass(frozen=True, slots=True)
class TeamHandoffAcceptanceCheck:
    check_id: str
    check_kind: str
    expected: str
    status: str = "pending"


@dataclass(frozen=True, slots=True)
class TeamHandoffContract:
    handoff_id: str
    correlation_id: str
    organization_id: str
    goal_id: str
    producer_unit_id: str
    producer_team_id: str
    producer_role_slot_id: str
    producer_task_id: str
    consumer_unit_id: str
    consumer_team_id: str
    consumer_role_slot_id: str
    consumer_task_id: str
    artifact_refs: tuple[TeamHandoffArtifactRef, ...]
    acceptance_checks: tuple[str | TeamHandoffAcceptanceCheck, ...]
    due_at: str
    sla_seconds: int
    handoff_definition_ref: str = ""
    acceptance_gate_ref: str = ""
    acceptance_gate_hash: str = ""
    acceptance_gate_allowed_decisions: tuple[str, ...] = ()
    acceptance_gate_self_approval_allowed: bool = False
    grounding_policy_ref: str = ""
    allowed_source_refs: tuple[str, ...] = ()
    allowed_run_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    grounding_verification_status: str = ""


@dataclass(frozen=True, slots=True)
class TeamHandoffDecision:
    handoff_id: str
    status: str
    reason_code: str
    revision: int
    event_id: str
    artifact_digests: tuple[str, ...]
    replayed: bool = False


class TeamHandoffService:
    """Validates transitions and persists references, never artifact bodies."""

    FINAL_STATUSES = frozenset({"accepted", "rejected", "needs_changes", "cancelled"})
    _VERSIONED_REF = re.compile(r"^[a-z][a-z0-9_]*@[1-9][0-9]*$")
    _CONTENT_HASH = re.compile(r"^[a-f0-9]{64}$")
    _GROUNDING_REF = re.compile(r"^(?:SRC|RUN)_[0-9]{4}$")
    _REASON_CODE = re.compile(r"^[a-z][a-z0-9_.:-]{0,190}$")
    _CHECK_KINDS = frozenset(
        {
            "artifact_present",
            "digest_matches",
            "evidence_verified",
            "policy_gate",
            "human_approval",
        }
    )

    def __init__(
        self,
        *,
        artifacts: ArtifactVersionReader,
        evidence: EvidenceManifestVerifier,
        store: HandoffStateStore,
        separation_of_duties: SeparationOfDutiesService | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._evidence = evidence
        self._store = store
        self._sod = separation_of_duties or SeparationOfDutiesService()

    def submit(
        self,
        *,
        contract: TeamHandoffContract,
        assignment_id: str,
        dispatch_lease_id: str,
        idempotency_key: str,
    ) -> TeamHandoffDecision:
        if contract.handoff_definition_ref:
            contract = replace(
                contract,
                acceptance_checks=tuple(
                    replace(check, status="pending") if isinstance(check, TeamHandoffAcceptanceCheck) else check
                    for check in contract.acceptance_checks
                ),
            )
        issues = self._validate_contract(contract)
        if not str(idempotency_key or "").strip():
            issues.append("handoff_idempotency_key_missing")
        contract_payload = self._contract_payload(contract)
        existing = self._store.get(contract.handoff_id)
        if existing:
            if str(existing.get("idempotency_key")) == idempotency_key and self._same_client_contract(
                existing.get("contract"), contract_payload
            ):
                return self._decision_from_state(
                    contract.handoff_id,
                    existing,
                    idempotency_key,
                    replayed=True,
                )
            return TeamHandoffDecision(
                contract.handoff_id,
                "conflict",
                "handoff_idempotency_conflict",
                int(existing.get("revision") or 0),
                self._event_id(contract.handoff_id, idempotency_key, "conflict"),
                tuple(existing.get("artifact_digests") or ()),
            )
        if issues:
            return TeamHandoffDecision(
                handoff_id=contract.handoff_id,
                status="blocked",
                reason_code=sorted(set(issues))[0],
                revision=0,
                event_id=self._event_id(contract.handoff_id, idempotency_key, "blocked"),
                artifact_digests=(),
            )
        verified_digests: list[str] = []
        verified_evidence_refs: set[str] = set()
        for reference in contract.artifact_refs:
            artifact = self._artifacts.get_verified_version(
                goal_id=contract.goal_id,
                artifact_id=reference.artifact_id,
                version=reference.artifact_version_ref or reference.version,
            )
            if artifact is None:
                issues.append(f"artifact_version_not_found:{reference.artifact_id}:{reference.version}")
                continue
            if artifact.digest != reference.digest:
                issues.append(f"artifact_digest_stale:{reference.artifact_id}")
            if artifact.verification_status not in {"verified", "accepted", "hub_verified"}:
                issues.append(f"artifact_not_verified:{reference.artifact_id}")
            if contract.handoff_definition_ref and artifact.producer_task_id != contract.producer_task_id:
                issues.append(f"artifact_producer_task_mismatch:{reference.artifact_id}")
            verified_evidence_refs.update(artifact.evidence_refs)
            evidence_allowed, evidence_reasons = self._evidence.verify(
                evidence_refs=artifact.evidence_refs,
                context_scope_refs=artifact.context_scope_refs,
                assignment_id=assignment_id,
                dispatch_lease_id=dispatch_lease_id,
            )
            if not evidence_allowed:
                issues.extend(evidence_reasons or ("artifact_evidence_not_allowed",))
            verified_digests.append(artifact.digest)

        if contract.evidence_refs and set(contract.evidence_refs) != verified_evidence_refs:
            issues.append("handoff_grounding_manifest_mismatch")

        if issues:
            return TeamHandoffDecision(
                handoff_id=contract.handoff_id,
                status="blocked",
                reason_code=sorted(set(issues))[0],
                revision=0,
                event_id=self._event_id(contract.handoff_id, idempotency_key, "blocked"),
                artifact_digests=tuple(sorted(verified_digests)),
            )
        if contract.handoff_definition_ref:
            # Status values in the HTTP contract are advisory input only.  A
            # definition-bound handoff reaches this point exclusively after
            # the Hub artifact reader, digest comparison and evidence verifier
            # succeeded, so the persisted gate checks are server-owned facts.
            contract = replace(
                contract,
                acceptance_checks=tuple(
                    replace(check, status="passed")
                    for check in contract.acceptance_checks
                    if isinstance(check, TeamHandoffAcceptanceCheck)
                ),
            )
            contract_payload = self._contract_payload(contract)
        payload = {
            "contract": contract_payload,
            "status": "pending_acceptance",
            "reason_code": "handoff_submitted",
            "revision": 1,
            "idempotency_key": idempotency_key,
            "artifact_digests": sorted(verified_digests),
            "updated_at": _now_iso(),
        }
        if not self._store.save_if_revision(contract.handoff_id, 0, payload):
            raced = self._store.get(contract.handoff_id)
            if (
                raced
                and str(raced.get("idempotency_key")) == idempotency_key
                and raced.get("contract") == payload["contract"]
            ):
                return self._decision_from_state(
                    contract.handoff_id,
                    raced,
                    idempotency_key,
                    replayed=True,
                )
            return TeamHandoffDecision(
                contract.handoff_id,
                "conflict",
                "handoff_submit_race",
                int((raced or {}).get("revision") or 0),
                self._event_id(contract.handoff_id, idempotency_key, "race"),
                tuple((raced or {}).get("artifact_digests") or ()),
            )
        return self._decision_from_state(contract.handoff_id, payload, idempotency_key)

    def decide(
        self,
        *,
        handoff_id: str,
        decision: str,
        reason_code: str,
        actor_principal_id: str,
        expected_revision: int,
        idempotency_key: str,
        duty_assignments: Iterable[DutyAssignment],
        sod_policy: SeparationOfDutiesPolicy,
        exception_ref: str | None = None,
    ) -> TeamHandoffDecision:
        normalized_decision = str(decision or "").strip().lower()
        if not str(idempotency_key or "").strip() or not str(actor_principal_id or "").strip():
            return TeamHandoffDecision(
                handoff_id,
                "blocked",
                "handoff_decision_binding_missing",
                expected_revision,
                "",
                (),
            )
        if normalized_decision not in {"accepted", "rejected", "needs_changes"}:
            return TeamHandoffDecision(handoff_id, "blocked", "handoff_decision_invalid", expected_revision, "", ())
        if self._REASON_CODE.fullmatch(str(reason_code or "").strip()) is None:
            return TeamHandoffDecision(
                handoff_id, "blocked", "handoff_structured_reason_required", expected_revision, "", ()
            )
        current = self._store.get(handoff_id)
        if current is None:
            return TeamHandoffDecision(handoff_id, "blocked", "handoff_not_found", 0, "", ())
        if current.get("status") in self.FINAL_STATUSES:
            if (
                current.get("decision_idempotency_key") == idempotency_key
                and current.get("status") == normalized_decision
            ):
                return self._decision_from_state(
                    handoff_id,
                    current,
                    idempotency_key,
                    replayed=True,
                )
            return TeamHandoffDecision(
                handoff_id,
                "conflict",
                "handoff_already_final",
                expected_revision,
                self._event_id(handoff_id, idempotency_key, "final"),
                tuple(current.get("artifact_digests") or ()),
            )
        if int(current.get("revision") or 0) != int(expected_revision):
            return TeamHandoffDecision(
                handoff_id,
                "conflict",
                "handoff_revision_stale",
                int(current.get("revision") or 0),
                self._event_id(handoff_id, idempotency_key, "stale"),
                tuple(current.get("artifact_digests") or ()),
            )
        contract = dict(current.get("contract") or {})
        handoff_definition_ref = str(contract.get("handoff_definition_ref") or "")
        if handoff_definition_ref:
            allowed_decisions = {
                str(value).strip().lower()
                for value in list(contract.get("acceptance_gate_allowed_decisions") or [])
                if str(value).strip()
            }
            decision_allowed = normalized_decision in allowed_decisions or (
                normalized_decision == "accepted" and "pass" in allowed_decisions
            )
            if not decision_allowed:
                return TeamHandoffDecision(
                    handoff_id,
                    "blocked",
                    "handoff_acceptance_gate_decision_not_allowed",
                    expected_revision,
                    self._event_id(handoff_id, idempotency_key, "gate-decision"),
                    tuple(current.get("artifact_digests") or ()),
                )
            if normalized_decision == "accepted":
                gate_ref = str(contract.get("acceptance_gate_ref") or "")
                checks = list(contract.get("acceptance_checks") or [])
                structured_checks = [value for value in checks if isinstance(value, dict)]
                policy_checks = [
                    value
                    for value in structured_checks
                    if str(value.get("check_kind") or "") == "policy_gate"
                    and str(value.get("expected") or "") == gate_ref
                ]
                if (
                    len(structured_checks) != len(checks)
                    or not structured_checks
                    or any(str(value.get("status") or "") != "passed" for value in structured_checks)
                    or not policy_checks
                ):
                    return TeamHandoffDecision(
                        handoff_id,
                        "blocked",
                        "handoff_acceptance_gate_checks_not_passed",
                        expected_revision,
                        self._event_id(handoff_id, idempotency_key, "gate-checks"),
                        tuple(current.get("artifact_digests") or ()),
                    )
        sod = self._sod.enforce_runtime_operation(
            operation="handoff_accept",
            actor_principal_id=actor_principal_id,
            source_assignments=tuple(duty_assignments),
            policy=sod_policy,
            exception_ref=exception_ref,
        )
        if not sod.allowed:
            return TeamHandoffDecision(
                handoff_id,
                "blocked",
                sod.reason_code,
                expected_revision,
                self._event_id(handoff_id, idempotency_key, "sod"),
                tuple(current.get("artifact_digests") or ()),
            )
        next_state = {
            **current,
            "status": normalized_decision,
            "reason_code": reason_code,
            "revision": expected_revision + 1,
            "decision_idempotency_key": idempotency_key,
            "decided_by_principal_id": actor_principal_id,
            "updated_at": _now_iso(),
        }
        if not self._store.save_if_revision(handoff_id, expected_revision, next_state):
            raced = self._store.get(handoff_id)
            if (
                raced
                and raced.get("decision_idempotency_key") == idempotency_key
                and raced.get("status") == normalized_decision
            ):
                return self._decision_from_state(
                    handoff_id,
                    raced,
                    idempotency_key,
                    replayed=True,
                )
            return TeamHandoffDecision(
                handoff_id,
                "conflict",
                "handoff_revision_race",
                expected_revision,
                self._event_id(handoff_id, idempotency_key, "race"),
                tuple((raced or current).get("artifact_digests") or ()),
            )
        return self._decision_from_state(handoff_id, next_state, idempotency_key)

    @staticmethod
    def _validate_contract(contract: TeamHandoffContract) -> list[str]:
        issues: list[str] = []
        required = (
            contract.handoff_id,
            contract.correlation_id,
            contract.organization_id,
            contract.goal_id,
            contract.producer_unit_id,
            contract.producer_team_id,
            contract.producer_role_slot_id,
            contract.producer_task_id,
            contract.consumer_unit_id,
            contract.consumer_team_id,
            contract.consumer_role_slot_id,
            contract.consumer_task_id,
            contract.due_at,
        )
        if any(not str(value or "").strip() for value in required):
            issues.append("handoff_binding_missing")
        if not contract.artifact_refs:
            issues.append("handoff_artifact_refs_empty")
        if not contract.acceptance_checks:
            issues.append("handoff_acceptance_checks_empty")
        for check in contract.acceptance_checks:
            if isinstance(check, str):
                if not check.strip():
                    issues.append("handoff_acceptance_check_invalid")
                continue
            if (
                not check.check_id
                or check.check_kind not in TeamHandoffService._CHECK_KINDS
                or not check.expected
                or check.status not in {"pending", "passed", "failed"}
            ):
                issues.append("handoff_acceptance_check_invalid")
        if (
            contract.handoff_definition_ref
            and TeamHandoffService._VERSIONED_REF.fullmatch(contract.handoff_definition_ref) is None
        ):
            issues.append("handoff_definition_ref_invalid")
        if contract.handoff_definition_ref:
            if (
                TeamHandoffService._VERSIONED_REF.fullmatch(contract.acceptance_gate_ref) is None
                or TeamHandoffService._CONTENT_HASH.fullmatch(contract.acceptance_gate_hash) is None
                or not contract.acceptance_gate_allowed_decisions
            ):
                issues.append("handoff_acceptance_gate_binding_invalid")
            if any(not isinstance(check, TeamHandoffAcceptanceCheck) for check in contract.acceptance_checks):
                issues.append("handoff_acceptance_gate_checks_unstructured")
            policy_checks = [
                check
                for check in contract.acceptance_checks
                if isinstance(check, TeamHandoffAcceptanceCheck)
                and check.check_kind == "policy_gate"
                and check.expected == contract.acceptance_gate_ref
            ]
            if len(policy_checks) != 1:
                issues.append("handoff_acceptance_gate_policy_check_invalid")
        grounding_values = (
            contract.grounding_policy_ref,
            contract.allowed_source_refs,
            contract.allowed_run_refs,
            contract.evidence_refs,
            contract.grounding_verification_status,
        )
        if any(grounding_values):
            if (
                TeamHandoffService._VERSIONED_REF.fullmatch(contract.grounding_policy_ref) is None
                or contract.grounding_verification_status != "hub_verified"
                or not contract.evidence_refs
            ):
                issues.append("handoff_grounding_binding_invalid")
            declared = (*contract.allowed_source_refs, *contract.allowed_run_refs)
            if (
                any(TeamHandoffService._GROUNDING_REF.fullmatch(value) is None for value in declared)
                or any(not value.startswith("SRC_") for value in contract.allowed_source_refs)
                or any(not value.startswith("RUN_") for value in contract.allowed_run_refs)
                or len(set(declared)) != len(declared)
                or len(set(contract.evidence_refs)) != len(contract.evidence_refs)
                or any(value not in set(declared) for value in contract.evidence_refs)
            ):
                issues.append("handoff_grounding_allowlist_invalid")
        for reference in contract.artifact_refs:
            if (
                not reference.artifact_id
                or not reference.version
                or not reference.digest
                or not reference.artifact_kind
                or reference.verification_status != "hub_verified"
            ):
                issues.append("handoff_artifact_ref_invalid")
        if contract.sla_seconds <= 0:
            issues.append("handoff_sla_invalid")
        elif contract.sla_seconds > 31_536_000:
            issues.append("handoff_sla_invalid")
        try:
            due_at = datetime.fromisoformat(contract.due_at.replace("Z", "+00:00"))
            if due_at.tzinfo is None:
                issues.append("handoff_due_at_timezone_required")
        except ValueError:
            issues.append("handoff_due_at_invalid")
        artifact_keys = {(reference.artifact_id, reference.version) for reference in contract.artifact_refs}
        if len(artifact_keys) != len(contract.artifact_refs):
            issues.append("handoff_artifact_ref_duplicate")
        return issues

    @staticmethod
    def _contract_payload(contract: TeamHandoffContract) -> dict[str, Any]:
        # A SQL JSON round-trip normalizes tuples to arrays.  Canonicalize at
        # the domain boundary so idempotency comparison is adapter-neutral.
        return json.loads(json.dumps(asdict(contract), sort_keys=True))

    @staticmethod
    def _same_client_contract(existing: Any, incoming: Mapping[str, Any]) -> bool:
        if not isinstance(existing, Mapping):
            return False
        server_fields = {
            "acceptance_gate_ref",
            "acceptance_gate_hash",
            "acceptance_gate_allowed_decisions",
            "acceptance_gate_self_approval_allowed",
        }
        if str(existing.get("handoff_definition_ref") or ""):
            server_fields.add("acceptance_checks")
        existing_client = {key: value for key, value in existing.items() if key not in server_fields}
        incoming_client = {key: value for key, value in incoming.items() if key not in server_fields}
        return existing_client == incoming_client

    @staticmethod
    def _event_id(handoff_id: str, idempotency_key: str, status: str) -> str:
        value = f"{handoff_id}:{idempotency_key}:{status}".encode()
        return f"handoff-event-{hashlib.sha256(value).hexdigest()[:24]}"

    def _decision_from_state(
        self,
        handoff_id: str,
        state: dict,
        idempotency_key: str,
        *,
        replayed: bool = False,
    ) -> TeamHandoffDecision:
        status = str(state.get("status") or "unknown")
        return TeamHandoffDecision(
            handoff_id=handoff_id,
            status=status,
            reason_code=str(state.get("reason_code") or ""),
            revision=int(state.get("revision") or 0),
            event_id=self._event_id(handoff_id, idempotency_key, status),
            artifact_digests=tuple(state.get("artifact_digests") or ()),
            replayed=replayed,
        )


class InMemoryHandoffStateStore:
    """Deterministic development/test adapter with compare-and-swap semantics."""

    def __init__(self) -> None:
        self._values: dict[str, dict] = {}

    def get(self, handoff_id: str) -> dict | None:
        value = self._values.get(handoff_id)
        return dict(value) if value is not None else None

    def save_if_revision(self, handoff_id: str, expected_revision: int, value: dict) -> bool:
        current = self._values.get(handoff_id)
        revision = int(current.get("revision") or 0) if current else 0
        if revision != expected_revision:
            return False
        self._values[handoff_id] = dict(value)
        return True


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "InMemoryHandoffStateStore",
    "TeamHandoffAcceptanceCheck",
    "TeamHandoffArtifactRef",
    "TeamHandoffContract",
    "TeamHandoffDecision",
    "TeamHandoffService",
]
