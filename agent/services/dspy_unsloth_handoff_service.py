"""Produces a governed dataset manifest; it never creates a training job."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from agent.services.dspy_evaluation_attestation_service import DspyEvaluationAttestationService
from ananta_contracts.dspy_optimization import DatasetManifestV1, canonical_digest, require_digest, require_id

_SECRET_MARKERS = ("api_key=", "authorization: bearer", "-----begin private key-----")


class DspyUnslothHandoffService:
    def __init__(self, attestations: DspyEvaluationAttestationService) -> None:
        self._attestations = attestations

    def export(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        accepted_examples: Sequence[Mapping[str, Any]],
        evaluation: Mapping[str, Any],
        security_gate_passed: bool,
        license_id: str,
        run_id: str,
        program_digest: str,
        model_set_digest: str,
        metric_digest: str,
    ) -> dict[str, Any]:
        if (
            not self._attestations.verify(evaluation)
            or not evaluation.get("promotion_eligible")
            or evaluation.get("candidate_program_digest") != program_digest
            or not security_gate_passed
        ):
            raise PermissionError("dspy_unsloth_handoff_gate_failed")
        require_id(run_id, "run_id")
        for value, field in (
            (program_digest, "program_digest"),
            (model_set_digest, "model_set_digest"),
            (metric_digest, "metric_digest"),
            (evaluation.get("evaluation_digest"), "evaluation_digest"),
        ):
            require_digest(value, field)
        if not 3 <= len(accepted_examples) <= 100_000:
            raise ValueError("dspy_unsloth_handoff_size_invalid")
        records: list[dict[str, Any]] = []
        record_ids: list[str] = []
        fingerprints: set[str] = set()
        for index, raw in enumerate(accepted_examples):
            if set(raw) - {"input", "output", "label_origin", "source_ref"}:
                raise ValueError("dspy_unsloth_handoff_record_invalid")
            origin = str(raw.get("label_origin") or "")
            if origin not in {"human", "synthetic_teacher"}:
                raise ValueError("dspy_unsloth_handoff_label_origin_invalid")
            if raw.get("source_ref") and not str(raw["source_ref"]).startswith("SRC_"):
                raise ValueError("dspy_unsloth_handoff_source_invalid")
            rendered = canonical_digest({"input": raw.get("input"), "output": raw.get("output")})
            if rendered in fingerprints:
                raise ValueError("dspy_unsloth_handoff_duplicate")
            fingerprints.add(rendered)
            content = f"{raw.get('input', '')}\n{raw.get('output', '')}".lower()
            if any(marker in content for marker in _SECRET_MARKERS) or "@" in content:
                raise ValueError("dspy_unsloth_handoff_sensitive_content")
            record = {**dict(raw), "record_id": f"record-{index:08d}", "synthetic": origin == "synthetic_teacher"}
            records.append(record)
            record_ids.append(record["record_id"])
        train_end = max(1, int(len(records) * 0.6))
        validation_end = max(train_end + 1, int(len(records) * 0.8))
        splits = {
            "train": record_ids[:train_end],
            "validation": record_ids[train_end:validation_end],
            "test": record_ids[validation_end:],
        }
        if not all(splits.values()):
            raise ValueError("dspy_unsloth_handoff_split_invalid")
        content_digest = canonical_digest(records)
        manifest = DatasetManifestV1(
            tenant_id=require_id(tenant_id, "tenant_id"),
            dataset_id=require_id(dataset_id, "dataset_id"),
            version=1,
            content_digest=content_digest,
            record_schema_digest=canonical_digest(["input", "output", "label_origin", "source_ref"]),
            split_digests={key: canonical_digest(value) for key, value in splits.items()},
            split_record_ids=splits,
            license_id=license_id,
            sensitivity="internal",
            retention_days=90,
            source_refs=tuple(sorted({str(item["source_ref"]) for item in records if item.get("source_ref")})),
            lineage={
                "run_id": run_id,
                "program_digest": program_digest,
                "model_set_digest": model_set_digest,
                "metric_digest": metric_digest,
                "evaluation_digest": str(evaluation["evaluation_digest"]),
            },
        )
        return {
            "manifest": {**manifest.__dict__} if hasattr(manifest, "__dict__") else _manifest_dict(manifest),
            "manifest_digest": manifest.digest,
            "records": records,
            "training_job_created": False,
            "promotion_performed": False,
            "human_intervention_required": False,
        }


def _manifest_dict(value: DatasetManifestV1) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(value)


__all__ = ["DspyUnslothHandoffService"]
