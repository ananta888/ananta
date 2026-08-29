"""Produces a governed dataset manifest; it never creates a training job."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ananta_contracts.dspy_optimization import DatasetManifestV1, canonical_digest, require_id


class DspyUnslothHandoffService:
    def export(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        accepted_examples: Sequence[Mapping[str, Any]],
        evaluation: Mapping[str, Any],
        security_gate_passed: bool,
        license_id: str,
    ) -> dict[str, Any]:
        if not evaluation.get("promotion_eligible") or not security_gate_passed:
            raise PermissionError("dspy_unsloth_handoff_gate_failed")
        if not 3 <= len(accepted_examples) <= 100_000:
            raise ValueError("dspy_unsloth_handoff_size_invalid")
        records: list[dict[str, Any]] = []
        record_ids: list[str] = []
        for index, raw in enumerate(accepted_examples):
            if set(raw) - {"input", "output", "label_origin", "source_ref"}:
                raise ValueError("dspy_unsloth_handoff_record_invalid")
            origin = str(raw.get("label_origin") or "")
            if origin not in {"human", "synthetic_teacher"}:
                raise ValueError("dspy_unsloth_handoff_label_origin_invalid")
            if raw.get("source_ref") and not str(raw["source_ref"]).startswith("SRC_"):
                raise ValueError("dspy_unsloth_handoff_source_invalid")
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
