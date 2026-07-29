"""Deterministic, tenant-scoped Data Recipe projection for Unsloth jobs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
import re
from typing import Literal, Protocol

from ananta_contracts.unsloth_task import (
    canonical_unsloth_json,
    unsloth_payload_sha256,
)
from agent.services.unsloth_evidence import ProvidedEvidenceRegistry


class DataRecipeValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DatasetSnapshot:
    dataset_id: str
    tenant_id: str
    dataset_hash: str
    dataset_ref: str
    dataset_partition_sha256: str
    state: str
    secret_scan_state: str
    pii_state: str
    license_state: str
    row_count: int


class DatasetCatalogPort(Protocol):
    def get_snapshot(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
    ) -> DatasetSnapshot | None: ...


@dataclass(frozen=True)
class DataRecipeRequest:
    tenant_id: str
    dataset_id: str
    source_id: str
    run_id: str
    objective: Literal[
        "causal_lm",
        "vision_instruction",
        "audio_instruction",
        "embedding_pairs",
    ]
    prompt_field: str
    response_field: str
    validation_fraction: float = 0.05
    seed: int = 3407
    media_field: str | None = None


@dataclass(frozen=True)
class DataRecipeManifest:
    recipe_id: str
    tenant_id: str
    dataset_id: str
    dataset_hash: str
    dataset_ref: str
    dataset_partition_sha256: str
    source_id: str
    run_id: str
    objective: str
    prompt_field: str
    response_field: str
    media_field: str | None
    validation_fraction: float
    seed: int
    row_count: int
    normalization_version: str = "unsloth-recipe-v2"

    def canonical_json(self) -> str:
        return canonical_unsloth_json(asdict(self))


class UnslothDataRecipeAdapter:
    """Builds immutable recipe manifests without reading dataset contents."""

    _HASH_LENGTH = 64
    _FIELD_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")

    def __init__(
        self,
        *,
        datasets: DatasetCatalogPort,
        evidence: ProvidedEvidenceRegistry,
    ) -> None:
        self._datasets = datasets
        self._evidence = evidence

    def build(self, request: DataRecipeRequest) -> DataRecipeManifest:
        self._validate_request(request)
        snapshot = self._datasets.get_snapshot(
            tenant_id=request.tenant_id,
            dataset_id=request.dataset_id,
        )
        if snapshot is None:
            raise DataRecipeValidationError(
                "dataset_not_found",
                "The dataset is unavailable in the tenant catalog.",
            )
        self._validate_snapshot(request, snapshot)
        source_id = self._evidence.require_source(request.source_id)
        run_id = self._evidence.require_run(request.run_id)
        unsigned = {
            "tenant_id": request.tenant_id,
            "dataset_id": snapshot.dataset_id,
            "dataset_hash": snapshot.dataset_hash,
            "dataset_ref": snapshot.dataset_ref,
            "dataset_partition_sha256": snapshot.dataset_partition_sha256,
            "source_id": source_id,
            "run_id": run_id,
            "objective": request.objective,
            "prompt_field": request.prompt_field,
            "response_field": request.response_field,
            "media_field": request.media_field,
            "validation_fraction": request.validation_fraction,
            "seed": request.seed,
            "row_count": snapshot.row_count,
            "normalization_version": "unsloth-recipe-v2",
        }
        digest = unsloth_payload_sha256(unsigned)
        return DataRecipeManifest(recipe_id=digest, **unsigned)

    @classmethod
    def _validate_request(cls, request: DataRecipeRequest) -> None:
        if not request.tenant_id or not request.dataset_id:
            raise DataRecipeValidationError(
                "dataset_scope_missing",
                "Tenant and dataset IDs are required.",
            )
        if any(
            not isinstance(value, str)
            or cls._FIELD_NAME.fullmatch(value) is None
            for value in (
                request.prompt_field,
                request.response_field,
            )
        ):
            raise DataRecipeValidationError(
                "dataset_mapping_invalid",
                "Prompt and response fields must be bounded JSON field names.",
            )
        if request.objective not in {
            "causal_lm",
            "vision_instruction",
            "audio_instruction",
            "embedding_pairs",
        }:
            raise DataRecipeValidationError(
                "dataset_objective_invalid",
                "The requested dataset objective is unsupported.",
            )
        if (
            isinstance(request.validation_fraction, bool)
            or not isinstance(request.validation_fraction, (int, float))
            or not 0.0 < float(request.validation_fraction) < 0.5
        ):
            raise DataRecipeValidationError(
                "dataset_split_invalid",
                "Validation fraction must be greater than zero and below 0.5.",
            )
        if (
            isinstance(request.seed, bool)
            or not isinstance(request.seed, int)
            or not 0 <= request.seed <= 2**31 - 1
        ):
            raise DataRecipeValidationError(
                "dataset_seed_invalid",
                "The deterministic split seed is outside its supported bounds.",
            )
        is_multimodal = request.objective in {
            "vision_instruction",
            "audio_instruction",
        }
        media_field_valid = (
            isinstance(request.media_field, str)
            and cls._FIELD_NAME.fullmatch(request.media_field)
            is not None
        )
        if (
            is_multimodal
            and not media_field_valid
        ) or (
            not is_multimodal
            and request.media_field is not None
        ):
            raise DataRecipeValidationError(
                "dataset_media_mapping_invalid",
                "Multimodal objectives require exactly one media field.",
            )

    @classmethod
    def _validate_snapshot(
        cls,
        request: DataRecipeRequest,
        snapshot: DatasetSnapshot,
    ) -> None:
        if snapshot.tenant_id != request.tenant_id:
            raise DataRecipeValidationError(
                "dataset_tenant_mismatch",
                "The dataset does not belong to the requested tenant.",
            )
        if snapshot.state != "approved":
            raise DataRecipeValidationError(
                "dataset_not_approved",
                "Only approved dataset snapshots can be used.",
            )
        if snapshot.secret_scan_state != "passed":
            raise DataRecipeValidationError(
                "dataset_secret_scan_failed",
                "The dataset must pass secret scanning.",
            )
        if snapshot.pii_state != "clear":
            raise DataRecipeValidationError(
                "dataset_pii_review_required",
                "The dataset must be clear of unapproved personal data.",
            )
        if snapshot.license_state != "approved":
            raise DataRecipeValidationError(
                "dataset_license_not_approved",
                "The dataset license must be approved.",
            )
        if (
            len(snapshot.dataset_hash) != cls._HASH_LENGTH
            or any(char not in "0123456789abcdef" for char in snapshot.dataset_hash)
            or len(snapshot.dataset_partition_sha256) != cls._HASH_LENGTH
            or any(
                char not in "0123456789abcdef"
                for char in snapshot.dataset_partition_sha256
            )
        ):
            raise DataRecipeValidationError(
                "dataset_hash_invalid",
                "The dataset snapshot requires a lowercase SHA-256 hash.",
            )
        dataset_ref = PurePosixPath(snapshot.dataset_ref)
        if (
            not snapshot.dataset_ref
            or dataset_ref.is_absolute()
            or ".." in dataset_ref.parts
            or snapshot.dataset_ref != dataset_ref.as_posix()
        ):
            raise DataRecipeValidationError(
                "dataset_ref_invalid",
                "The dataset partition reference must be a safe root-relative path.",
            )
        if snapshot.row_count <= 0:
            raise DataRecipeValidationError(
                "dataset_empty",
                "The dataset snapshot must contain at least one row.",
            )
