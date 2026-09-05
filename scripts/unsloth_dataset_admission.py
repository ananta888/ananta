"""Deterministic local admission of a pinned, non-synthetic Unsloth dataset."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from agent.services.ml_intern_dataset_catalog_service import (
    MlInternDatasetCatalogService,
)
from agent.services.unsloth_data_recipe_adapter import (
    DataRecipeRequest,
    DatasetSnapshot,
    UnslothDataRecipeAdapter,
)
from agent.services.unsloth_evidence import ProvidedEvidenceRegistry
from worker.training.data_recipe_materializer import (
    FilesystemDatasetRecipeMaterializer,
)


class UnslothDatasetAdmissionError(ValueError):
    """Bounded failure for an immutable dataset admission."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_contract(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UnslothDatasetAdmissionError("dataset_contract_unavailable") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != "ananta.unsloth-dataset-admission.v1"
        or not isinstance(value.get("selection"), dict)
        or not isinstance(value.get("license"), dict)
    ):
        raise UnslothDatasetAdmissionError("dataset_contract_invalid")
    return value


def _source_candidates(source: Path, contract: Mapping[str, Any]) -> list[dict[str, str]]:
    selection = dict(contract["selection"])
    allowed = frozenset(str(value) for value in selection.get("allowed_categories") or ())
    candidate_limit = int(selection.get("candidate_limit") or 0)
    expected_records = int(contract.get("source_record_count") or 0)
    candidates: list[dict[str, str]] = []
    total = 0
    try:
        with source.open("r", encoding="utf-8") as handle:
            for line in handle:
                total += 1
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise UnslothDatasetAdmissionError("dataset_source_record_invalid")
                instruction = record.get("instruction")
                response = record.get("response")
                context = record.get("context")
                category = str(record.get("category") or "")
                if (
                    len(candidates) < candidate_limit
                    and category in allowed
                    and isinstance(instruction, str)
                    and instruction.strip()
                    and isinstance(response, str)
                    and response.strip()
                    and (
                        selection.get("require_empty_context") is not True
                        or (isinstance(context, str) and not context.strip())
                    )
                ):
                    candidates.append(
                        {
                            "instruction": instruction,
                            "input": "",
                            "output": response,
                        }
                    )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UnslothDatasetAdmissionError("dataset_source_invalid") from exc
    if total != expected_records or len(candidates) != candidate_limit:
        raise UnslothDatasetAdmissionError("dataset_source_record_count_mismatch")
    return candidates


def _catalog_validation(
    records: list[dict[str, str]],
    *,
    root: Path,
    dataset_id: str,
) -> tuple[MlInternDatasetCatalogService, dict[str, Any], dict[str, Any]]:
    service = MlInternDatasetCatalogService(
        storage_root=root,
        id_factory=lambda: dataset_id,
    )
    summary = service.create_from_records(
        tenant_id="ananta-local",
        principal_id="hub-evidence-gate",
        records=records,
        name="Databricks Dolly 15k local evaluation partition",
        dataset_format="instruction",
        idempotency_key=f"dolly-local:{dataset_id}",
    )
    report = service.validate_dataset(
        tenant_id="ananta-local",
        principal_id="hub-evidence-gate",
        dataset_id=summary["dataset_id"],
    )
    return service, summary, report


def _blocked_lines(report: Mapping[str, Any]) -> frozenset[int]:
    blocked = {
        int(item["line"])
        for item in report.get("pii_findings") or ()
        if isinstance(item, Mapping) and isinstance(item.get("line"), int)
    }
    train = report.get("train")
    if isinstance(train, Mapping):
        blocked.update(
            int(item["line"])
            for item in train.get("secret_findings") or ()
            if isinstance(item, Mapping) and isinstance(item.get("line"), int)
        )
    return frozenset(blocked)


def materialize_admitted_dolly_recipe(
    *,
    source_path: Path,
    contract_path: Path,
    output_root: Path,
    source_id: str,
    run_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    """Validate, curate and materialize one Hub-bound Dolly recipe."""
    contract = _load_contract(contract_path)
    if source_path.is_symlink():
        raise UnslothDatasetAdmissionError("dataset_source_binding_invalid")
    source = source_path.resolve(strict=True)
    if (
        sha256_file(source) != contract.get("source_sha256")
        or contract.get("license", {}).get("approved_scope") != "local_nonproduction_evaluation"
    ):
        raise UnslothDatasetAdmissionError("dataset_source_binding_invalid")
    candidates = _source_candidates(source, contract)
    output_root.mkdir(parents=True, exist_ok=True)
    scan_root = output_root / "candidate-scan"
    _, _, scan = _catalog_validation(
        candidates,
        root=scan_root,
        dataset_id="ds-" + hashlib.sha256(b"dolly-candidate-scan-v1").hexdigest()[:32],
    )
    blocked = _blocked_lines(scan)
    clean = [record for index, record in enumerate(candidates, start=1) if index not in blocked]
    required = int(contract["selection"].get("record_count") or 0)
    if len(clean) < required or required < 2:
        raise UnslothDatasetAdmissionError("dataset_clean_partition_too_small")
    selected = clean[:required]
    selection_digest = hashlib.sha256(
        json.dumps(selected, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    dataset_id = "ds-" + selection_digest[:32]
    catalog_root = output_root / "catalog"
    catalog, summary, validation = _catalog_validation(
        selected,
        root=catalog_root,
        dataset_id=dataset_id,
    )
    if validation.get("ok") is not True:
        raise UnslothDatasetAdmissionError("dataset_partition_validation_failed")
    dataset_root = output_root / "dataset"
    dataset_root.mkdir()
    partition = dataset_root / "dolly-local-evaluation.jsonl"
    with partition.open("wb") as target:
        catalog.copy_partition_to(
            tenant_id="ananta-local",
            principal_id="hub-evidence-gate",
            dataset_id=dataset_id,
            partition="train",
            destination=target,
        )
    partition_sha256 = sha256_file(partition)
    if partition_sha256 != summary.get("sha256"):
        raise UnslothDatasetAdmissionError("dataset_partition_export_mismatch")

    class SnapshotCatalog:
        def get_snapshot(self, *, tenant_id: str, dataset_id: str) -> DatasetSnapshot | None:
            if tenant_id != "ananta-local" or dataset_id != str(contract["dataset_id"]):
                return None
            return DatasetSnapshot(
                dataset_id=dataset_id,
                tenant_id=tenant_id,
                dataset_hash=str(contract["source_sha256"]),
                dataset_ref=partition.name,
                dataset_partition_sha256=partition_sha256,
                state="approved",
                secret_scan_state="passed",
                pii_state="clear",
                license_state="approved",
                row_count=int(summary["record_count"]),
            )

    manifest = UnslothDataRecipeAdapter(
        datasets=SnapshotCatalog(),
        evidence=ProvidedEvidenceRegistry(source_ids=(source_id,), run_ids=(run_id,)),
    ).build(
        DataRecipeRequest(
            tenant_id="ananta-local",
            dataset_id=str(contract["dataset_id"]),
            source_id=source_id,
            run_id=run_id,
            objective="causal_lm",
            prompt_field="instruction",
            response_field="output",
            validation_fraction=0.2,
            seed=3407,
        )
    )
    materialized_root = output_root / "materialized"
    materialized_root.mkdir()
    result = dict(
        FilesystemDatasetRecipeMaterializer(
            dataset_root=dataset_root,
            attempt_output_root=materialized_root,
            expected_attempt_id=attempt_id,
            max_dataset_bytes=16 * 1024 * 1024,
            max_output_bytes=32 * 1024 * 1024,
            max_records=1024,
        ).materialize(asdict(manifest), attempt_id=attempt_id)
    )
    return {
        "contract": contract,
        "contract_sha256": sha256_file(contract_path),
        "selection_sha256": selection_digest,
        "candidate_count": len(candidates),
        "excluded_sensitive_candidates": len(blocked),
        "validation": {
            "ok": validation["ok"],
            "reason_codes": list(validation.get("reason_codes") or ()),
            "pii_finding_count": int(validation.get("pii_finding_count") or 0),
            "secret_finding_count": int(validation.get("train", {}).get("secret_finding_count") or 0),
        },
        "manifest": asdict(manifest),
        "result": result,
        "result_path": str(materialized_root / manifest.recipe_id / "result.json"),
    }


__all__ = [
    "UnslothDatasetAdmissionError",
    "materialize_admitted_dolly_recipe",
    "sha256_file",
]
