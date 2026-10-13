"""Specialist benchmark projection without invented Base-vs-Adapter losses."""

import hashlib
import json
from pathlib import Path

from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from agent.services.ml_intern_adapter_registry_service import AdapterRecord
from agent.services.ml_intern_artifact_security_service import MlInternArtifactSecurityService
from agent.services.specialist_evaluation_projection import specialist_evaluation_projection
from agent.services.specialist_model_factory import CandidateOutcome
from agent.services.specialist_prediction_storage import ConfinedSpecialistPredictionStorage
from agent.services.specialist_promotion_gate import PromotionDecision


def _closed_fields(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("specialist_benchmark_projection_duplicate_field")
    return result


class SpecialistBenchmarkProjectionStore:
    """Persist technical reports only; this store provides no promotion Evidence."""

    def __init__(self, *, artifact_root: Path):
        self._security = MlInternArtifactSecurityService(storage_root=artifact_root)
        self._files = ConfinedSpecialistPredictionStorage(artifact_root=artifact_root, dataset_root=artifact_root)
        self._transaction = InterProcessFileTransaction(artifact_root / "specialist-benchmarks.lock")

    @staticmethod
    def _reference(principal, identifier):
        tenant = hashlib.sha256(principal.tenant_id.encode()).hexdigest()
        owner = hashlib.sha256(principal.subject.encode()).hexdigest()
        return f"specialist-benchmarks/{tenant}/{owner}/{identifier}.json"

    def save(self, principal: MlInternTrainingPrincipal, winner: CandidateOutcome, decision: PromotionDecision) -> str:
        payload = specialist_evaluation_projection(winner, decision)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > 128 * 1024:
            raise ValueError("specialist_benchmark_projection_quota_exceeded")
        identifier = f"specialist-eval-{hashlib.sha256(encoded).hexdigest()}"
        with self._transaction:
            try:
                existing = self.get(principal, identifier)
            except FileNotFoundError:
                self._security.atomic_write_json(self._reference(principal, identifier), payload)
            else:
                if existing != payload:
                    raise ValueError("specialist_benchmark_projection_conflict")
        return identifier

    def get(self, principal: MlInternTrainingPrincipal, identifier: str) -> dict:
        if (
            not identifier.startswith("specialist-eval-") or len(identifier) != 80
            or any(value not in "0123456789abcdef" for value in identifier[16:])
        ):
            raise ValueError("specialist_benchmark_projection_identifier_invalid")
        with self._files.open_artifact(self._reference(principal, identifier)) as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ValueError("specialist_benchmark_projection_quota_exceeded")
        payload = json.loads(raw, object_pairs_hook=_closed_fields)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if f"specialist-eval-{hashlib.sha256(encoded).hexdigest()}" != identifier:
            raise ValueError("specialist_benchmark_projection_changed")
        return payload

    def bind(self, principal: MlInternTrainingPrincipal, record: AdapterRecord, reference: str) -> None:
        payload = self.get(principal, reference)
        self._require_record_binding(principal, record, payload)
        key = self._binding_key(record)
        binding = {"adapter_id": record.adapter_id, "benchmark_digest": record.eval_report_ref,
                   "report_ref": reference}
        with self._transaction:
            try:
                current = self._read_binding(principal, key)
            except FileNotFoundError:
                self._security.atomic_write_json(self._reference(principal, key), binding)
            else:
                if current != binding:
                    raise ValueError("specialist_benchmark_binding_conflict")

    def get_for_record(self, principal: MlInternTrainingPrincipal, record: AdapterRecord) -> dict:
        binding = self._read_binding(principal, self._binding_key(record))
        if (
            set(binding) != {"adapter_id", "benchmark_digest", "report_ref"}
            or binding["adapter_id"] != record.adapter_id or binding["benchmark_digest"] != record.eval_report_ref
        ):
            raise ValueError("specialist_benchmark_binding_changed")
        payload = self.get(principal, binding["report_ref"])
        self._require_record_binding(principal, record, payload)
        return payload

    def _read_binding(self, principal, key):
        with self._files.open_artifact(self._reference(principal, key)) as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError("specialist_benchmark_binding_quota_exceeded")
        return json.loads(raw, object_pairs_hook=_closed_fields)

    @staticmethod
    def _binding_key(record):
        raw = json.dumps([record.adapter_id, record.eval_report_ref], separators=(",", ":")).encode()
        return f"binding-{hashlib.sha256(raw).hexdigest()}"

    @staticmethod
    def _require_record_binding(principal, record, payload):
        lineage = payload.get("lineage") or {}
        if (
            record.tenant_id != principal.tenant_id or record.owner_subject != principal.subject
            or payload.get("artifact_sha256") != record.artifact_sha256
            or (payload.get("benchmark") or {}).get("result_digest") != record.eval_report_ref
            or record.adapter_id != f"specialist-{lineage.get('specialist_id')}-{lineage.get('version')}"
            or record.base_model != lineage.get("base_model") or record.method != lineage.get("method")
        ):
            raise ValueError("specialist_benchmark_registry_binding_invalid")
