"""Closed dataset and tokenizer contracts for research training.

These contracts contain no filesystem or ML-runtime behavior.  The Hub owns
admission and immutable identity; Workers consume only the resulting bounded
projection.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

from ananta_contracts.research_training import (
    ResearchTrainingContractError,
    canonical_digest,
    require_digest,
    require_id,
)

_SOURCE_ID = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9_.:-]{0,187}$")
_SPLITS = frozenset({"train", "validation", "test"})


def _closed(value: Mapping[str, Any], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ResearchTrainingContractError(f"research_{name}_fields_invalid")


def _sequence(value: object, name: str, *, minimum: int = 1, maximum: int = 4096) -> Sequence[Any]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not minimum <= len(value) <= maximum
    ):
        raise ResearchTrainingContractError(f"research_{name}_invalid")
    return value


def _relative_ref(value: object, name: str) -> str:
    text = str(value or "").strip()
    parts = text.split("/")
    if (
        not text
        or len(text) > 512
        or text.startswith("/")
        or "\\" in text
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ResearchTrainingContractError(f"research_{name}_invalid")
    return text


@dataclass(frozen=True, slots=True)
class ResearchDatasetShardV1:
    source_id: str
    relative_ref: str
    content_digest: str
    size_bytes: int
    split: str
    media_type: str
    license_id: str
    consent_class: str
    pii_scan_digest: str
    secret_scan_digest: str
    dedup_digest: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchDatasetShardV1:
        _closed(
            value,
            {
                "source_id",
                "relative_ref",
                "content_digest",
                "size_bytes",
                "split",
                "media_type",
                "license_id",
                "consent_class",
                "pii_scan_digest",
                "secret_scan_digest",
                "dedup_digest",
            },
            "dataset_shard",
        )
        source_id = str(value.get("source_id") or "").strip()
        split = str(value.get("split") or "").strip().lower()
        size = value.get("size_bytes")
        if _SOURCE_ID.fullmatch(source_id) is None:
            raise ResearchTrainingContractError("research_dataset_source_id_invalid")
        if split not in _SPLITS:
            raise ResearchTrainingContractError("research_dataset_split_invalid")
        if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 1 << 50:
            raise ResearchTrainingContractError("research_dataset_size_invalid")
        return cls(
            source_id=source_id,
            relative_ref=_relative_ref(value.get("relative_ref"), "dataset_relative_ref"),
            content_digest=require_digest(value.get("content_digest"), "dataset_content_digest"),
            size_bytes=size,
            split=split,
            media_type=require_id(value.get("media_type"), "dataset_media_type"),
            license_id=require_id(value.get("license_id"), "dataset_license_id"),
            consent_class=require_id(value.get("consent_class"), "dataset_consent_class"),
            pii_scan_digest=require_digest(value.get("pii_scan_digest"), "dataset_pii_scan_digest"),
            secret_scan_digest=require_digest(value.get("secret_scan_digest"), "dataset_secret_scan_digest"),
            dedup_digest=require_digest(value.get("dedup_digest"), "dataset_dedup_digest"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ResearchDatasetManifestV1:
    SCHEMA: ClassVar[str] = "ananta.research-training-dataset-manifest.v1"

    schema: str
    tenant_id: str
    project_id: str
    policy_digest: str
    contamination_check_digest: str
    shards: tuple[ResearchDatasetShardV1, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchDatasetManifestV1:
        _closed(
            value,
            {"schema", "tenant_id", "project_id", "policy_digest", "contamination_check_digest", "shards"},
            "dataset_manifest",
        )
        if value.get("schema") != cls.SCHEMA:
            raise ResearchTrainingContractError("research_dataset_manifest_schema_invalid")
        raw_shards = _sequence(value.get("shards"), "dataset_shards")
        if any(not isinstance(item, Mapping) for item in raw_shards):
            raise ResearchTrainingContractError("research_dataset_shards_invalid")
        shards = tuple(ResearchDatasetShardV1.from_mapping(item) for item in raw_shards)
        identities = [(item.source_id, item.relative_ref) for item in shards]
        if len(identities) != len(set(identities)):
            raise ResearchTrainingContractError("research_dataset_shard_duplicate")
        if "train" not in {item.split for item in shards}:
            raise ResearchTrainingContractError("research_dataset_train_split_missing")
        return cls(
            schema=cls.SCHEMA,
            tenant_id=require_id(value.get("tenant_id"), "tenant_id"),
            project_id=require_id(value.get("project_id"), "project_id"),
            policy_digest=require_digest(value.get("policy_digest"), "dataset_policy_digest"),
            contamination_check_digest=require_digest(
                value.get("contamination_check_digest"), "dataset_contamination_check_digest"
            ),
            shards=shards,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "policy_digest": self.policy_digest,
            "contamination_check_digest": self.contamination_check_digest,
            "shards": [item.to_dict() for item in self.shards],
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def size_bytes(self) -> int:
        return sum(item.size_bytes for item in self.shards)

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.source_id for item in self.shards}))


@dataclass(frozen=True, slots=True)
class ResearchTokenizerManifestV1:
    SCHEMA: ClassVar[str] = "ananta.research-training-tokenizer.v1"

    schema: str
    tokenizer_id: str
    algorithm: str
    artifact_digest: str
    dataset_manifest_digest: str
    vocab_size: int
    special_tokens: tuple[str, ...]
    normalizer: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchTokenizerManifestV1:
        _closed(
            value,
            {
                "schema",
                "tokenizer_id",
                "algorithm",
                "artifact_digest",
                "dataset_manifest_digest",
                "vocab_size",
                "special_tokens",
                "normalizer",
            },
            "tokenizer_manifest",
        )
        vocab_size = value.get("vocab_size")
        if value.get("schema") != cls.SCHEMA or value.get("algorithm") != "byte_bpe_v1":
            raise ResearchTrainingContractError("research_tokenizer_manifest_invalid")
        if not isinstance(vocab_size, int) or isinstance(vocab_size, bool) or not 256 <= vocab_size <= 1_048_576:
            raise ResearchTrainingContractError("research_tokenizer_vocab_size_invalid")
        raw_tokens = _sequence(value.get("special_tokens"), "tokenizer_special_tokens", maximum=128)
        tokens = tuple(str(item) for item in raw_tokens)
        if any(not item or len(item.encode("utf-8")) > 128 for item in tokens) or len(tokens) != len(set(tokens)):
            raise ResearchTrainingContractError("research_tokenizer_special_tokens_invalid")
        return cls(
            schema=cls.SCHEMA,
            tokenizer_id=require_id(value.get("tokenizer_id"), "tokenizer_id"),
            algorithm="byte_bpe_v1",
            artifact_digest=require_digest(value.get("artifact_digest"), "tokenizer_artifact_digest"),
            dataset_manifest_digest=require_digest(
                value.get("dataset_manifest_digest"), "dataset_manifest_digest"
            ),
            vocab_size=vocab_size,
            special_tokens=tokens,
            normalizer=require_id(value.get("normalizer"), "tokenizer_normalizer"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "special_tokens": list(self.special_tokens)}


__all__ = [
    "ResearchDatasetManifestV1",
    "ResearchDatasetShardV1",
    "ResearchTokenizerManifestV1",
]
