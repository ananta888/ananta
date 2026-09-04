"""Hub-side immutable dataset admission for research training."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.services.research_training_evidence_service import ResearchTrainingEvidenceService
from ananta_contracts.research_training import canonical_digest, require_id
from ananta_contracts.research_training_data import ResearchDatasetManifestV1

_EMAIL = re.compile(rb"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SECRET = re.compile(rb"(?i)(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_./+-]{12,}")


class ResearchTrainingDatasetService:
    def __init__(
        self,
        root: str | Path,
        *,
        evidence: ResearchTrainingEvidenceService,
        allowed_licenses: Sequence[str],
        maximum_dataset_bytes: int,
    ) -> None:
        self._root = Path(root).resolve()
        self._evidence = evidence
        self._licenses = frozenset(require_id(item, "license_id") for item in allowed_licenses)
        self._maximum = int(maximum_dataset_bytes)
        if not self._root.is_dir() or not self._licenses or not 1 <= self._maximum <= 1 << 50:
            raise ValueError("research_dataset_service_config_invalid")

    def admit(
        self,
        *,
        tenant_id: str,
        project_id: str,
        candidates: Sequence[Mapping[str, Any]],
        policy: Mapping[str, Any],
        evidence_scope: str,
        synthetic: bool = False,
    ) -> dict[str, Any]:
        if not 1 <= len(candidates) <= 4096:
            raise ValueError("research_dataset_candidates_invalid")
        shards: list[dict[str, Any]] = []
        split_fingerprints: dict[str, set[str]] = {"train": set(), "validation": set(), "test": set()}
        consumed = 0
        for candidate in candidates:
            if set(candidate) != {
                "origin",
                "relative_ref",
                "split",
                "media_type",
                "license_id",
                "consent_class",
            }:
                raise ValueError("research_dataset_candidate_fields_invalid")
            license_id = require_id(candidate.get("license_id"), "license_id")
            if license_id not in self._licenses:
                raise PermissionError("research_dataset_license_denied")
            split = str(candidate.get("split") or "").strip().lower()
            if split not in split_fingerprints:
                raise ValueError("research_dataset_split_invalid")
            relative_ref = self._relative_ref(candidate.get("relative_ref"))
            content = self._stable_read(relative_ref)
            consumed += len(content)
            if consumed > self._maximum:
                raise ValueError("research_dataset_size_exceeded")
            pii_matches = sorted(set(match.decode("utf-8", errors="replace") for match in _EMAIL.findall(content)))
            secret_matches = sorted(set(match.decode("utf-8", errors="replace") for match in _SECRET.findall(content)))
            pii_scan = {"schema": "ananta.research-pii-scan.v1", "matches": pii_matches}
            secret_scan = {"schema": "ananta.research-secret-scan.v1", "matches": secret_matches}
            if pii_matches:
                raise PermissionError("research_dataset_pii_detected")
            if secret_matches:
                raise PermissionError("research_dataset_secret_detected")
            normalized_records = [line.strip() for line in content.splitlines() if line.strip()]
            fingerprints = {hashlib.sha256(line).hexdigest() for line in normalized_records}
            if len(fingerprints) != len(normalized_records):
                raise PermissionError("research_dataset_duplicate_record_detected")
            if fingerprints & split_fingerprints[split]:
                raise PermissionError("research_dataset_duplicate_record_detected")
            split_fingerprints[split].update(fingerprints)
            origin = candidate.get("origin")
            if not isinstance(origin, Mapping):
                raise ValueError("research_dataset_origin_invalid")
            admitted = self._evidence.admit_source(
                tenant_id=tenant_id,
                project_id=project_id,
                origin_type="research_dataset_shard",
                origin={**dict(origin), "relative_ref": relative_ref},
                content=content,
                policy={**dict(policy), "license_id": license_id, "consent_class": candidate["consent_class"]},
                evidence_scope=evidence_scope,
                synthetic=synthetic,
            )
            shards.append(
                {
                    "source_id": admitted["source_id"],
                    "relative_ref": relative_ref,
                    "content_digest": admitted["content_digest"],
                    "size_bytes": len(content),
                    "split": split,
                    "media_type": require_id(candidate.get("media_type"), "dataset_media_type"),
                    "license_id": license_id,
                    "consent_class": require_id(candidate.get("consent_class"), "dataset_consent_class"),
                    "pii_scan_digest": canonical_digest(pii_scan),
                    "secret_scan_digest": canonical_digest(secret_scan),
                    "dedup_digest": canonical_digest(sorted(fingerprints)),
                }
            )
        contamination = {
            "schema": "ananta.research-contamination-check.v1",
            "train_validation_overlap": sorted(split_fingerprints["train"] & split_fingerprints["validation"]),
            "train_test_overlap": sorted(split_fingerprints["train"] & split_fingerprints["test"]),
            "validation_test_overlap": sorted(split_fingerprints["validation"] & split_fingerprints["test"]),
        }
        if any(contamination[key] for key in contamination if key != "schema"):
            raise PermissionError("research_dataset_split_contamination_detected")
        manifest = ResearchDatasetManifestV1.from_mapping(
            {
                "schema": ResearchDatasetManifestV1.SCHEMA,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "policy_digest": canonical_digest(policy),
                "contamination_check_digest": canonical_digest(contamination),
                "shards": shards,
            }
        )
        return manifest.to_dict()

    def _stable_read(self, relative_ref: str) -> bytes:
        target = (self._root / relative_ref).resolve()
        if self._root not in target.parents or not target.is_file() or target.is_symlink():
            raise PermissionError("research_dataset_ref_invalid")
        before = target.stat()
        if not 1 <= before.st_size <= self._maximum:
            raise ValueError("research_dataset_shard_size_invalid")
        content = target.read_bytes()
        after = target.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("research_dataset_shard_changed")
        return content

    @staticmethod
    def _relative_ref(value: object) -> str:
        text = str(value or "").strip()
        parts = text.split("/")
        if not text or text.startswith("/") or "\\" in text or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("research_dataset_relative_ref_invalid")
        return text


__all__ = ["ResearchTrainingDatasetService"]
