"""Shared evidence row projections and immutable terminal transition rules."""

import json
from dataclasses import asdict

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.ports.evidence_identity import RunEvidenceIdentity, SourceEvidenceIdentity


class EvidenceIdentityPersistenceError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def source_row(identity: SourceEvidenceIdentity) -> HubSourceEvidenceIdentityDB:
    return HubSourceEvidenceIdentityDB(**asdict(identity))


def run_row(identity: RunEvidenceIdentity) -> HubRunEvidenceIdentityDB:
    return HubRunEvidenceIdentityDB(**{**asdict(identity), "source_ids": list(identity.source_ids)})


def source_identity(row: HubSourceEvidenceIdentityDB) -> SourceEvidenceIdentity:
    return SourceEvidenceIdentity(**row.model_dump())


def run_identity(row: HubRunEvidenceIdentityDB) -> RunEvidenceIdentity:
    payload = row.model_dump()
    payload["source_ids"] = tuple(payload["source_ids"])
    return RunEvidenceIdentity(**payload)


def same_source(row: HubSourceEvidenceIdentityDB, identity: SourceEvidenceIdentity) -> SourceEvidenceIdentity:
    projected = source_identity(row)
    if _immutable_json(projected, {"created_at_epoch"}) != _immutable_json(identity, {"created_at_epoch"}):
        raise EvidenceIdentityPersistenceError("evidence_source_identity_immutable_conflict")
    return projected


def same_run(row: HubRunEvidenceIdentityDB, identity: RunEvidenceIdentity) -> RunEvidenceIdentity:
    projected = run_identity(row)
    mutable = {"state", "result_digest", "created_at_epoch", "updated_at_epoch"}
    if _immutable_json(projected, mutable) != _immutable_json(identity, mutable):
        raise EvidenceIdentityPersistenceError("evidence_run_identity_immutable_conflict")
    return projected


def _immutable_json(identity, mutable: set[str]) -> str:
    return json.dumps(
        {key: value for key, value in asdict(identity).items() if key not in mutable},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    )


def apply_run_result(
    row: HubRunEvidenceIdentityDB | None, *, assignment_id: str, dispatch_lease_id: str,
    terminal_state: str, result_digest: str, updated_at_epoch: float,
) -> None:
    if row is None:
        raise EvidenceIdentityPersistenceError("evidence_run_identity_not_found")
    if row.assignment_id != assignment_id or row.dispatch_lease_id != dispatch_lease_id:
        raise EvidenceIdentityPersistenceError("evidence_run_assignment_binding_mismatch")
    if row.state in {"succeeded", "failed", "cancelled"}:
        if row.state != terminal_state or row.result_digest != result_digest:
            raise EvidenceIdentityPersistenceError("evidence_run_terminal_replay_conflict")
        return
    if row.state != "reserved":
        raise EvidenceIdentityPersistenceError("evidence_run_state_invalid")
    row.state, row.result_digest, row.updated_at_epoch = terminal_state, result_digest, updated_at_epoch
