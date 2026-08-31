"""Immutable allowlist catalog for hash-bound external scientific skills."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from agent.services.scientific_skill_manifest_service import (
    AUTHORIZED_UPSTREAM_REPOSITORY,
    ScientificSkillManifest,
)
from agent.services.scientific_skill_risk_profile_service import (
    ScientificSkillOperatingMode,
    ScientificSkillRiskProfile,
)

CATALOG_SCHEMA_VERSION = "ananta.scientific-skill-catalog.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PIN = re.compile(r"^(?:[0-9a-f]{7,64}|v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?)$")
_CLASSIFICATION_ORDER = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
_MODE_ORDER = {
    ScientificSkillOperatingMode.DOCUMENTATION_ONLY: 0,
    ScientificSkillOperatingMode.READ_ONLY_RESEARCH: 1,
    ScientificSkillOperatingMode.CONTROLLED_EXECUTION: 2,
    ScientificSkillOperatingMode.BLOCKED: 3,
}
_ALLOWED_TOOLS = frozenset({"source_lookup", "artifact_read", "citation_search", "sandbox_task_request"})


class ScientificSkillCatalogError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class ScientificSkillCatalogEntryStatus(str, Enum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    DISABLED = "disabled"


class ScientificSkillNetworkProfile(str, Enum):
    DENIED = "denied"
    DECLARED_READ_ONLY = "declared-read-only"
    APPROVAL_REQUIRED = "approval-required"


class ScientificSkillApprovalLevel(str, Enum):
    NONE = "none"
    SOURCE_ACCESS = "source-access"
    TASK = "task"


@dataclass(frozen=True)
class ScientificSkillCatalogEntry:
    entry_id: str
    skill_name: str
    upstream_repository: str
    upstream_path: str
    upstream_pin: str
    skill_sha256: str
    risk_profile_digest: str
    status: ScientificSkillCatalogEntryStatus
    allowed_mode: ScientificSkillOperatingMode
    context_budget_tokens: int
    allowed_tools: tuple[str, ...]
    data_classification: str
    network_profile: ScientificSkillNetworkProfile
    allowed_network_targets: tuple[str, ...]
    approval_level: ScientificSkillApprovalLevel
    approval_receipt_digest: str | None

    @classmethod
    def create(
        cls,
        *,
        skill_name: str,
        upstream_path: str,
        upstream_pin: str,
        skill_sha256: str,
        risk_profile_digest: str,
        status: ScientificSkillCatalogEntryStatus,
        allowed_mode: ScientificSkillOperatingMode,
        context_budget_tokens: int,
        allowed_tools: tuple[str, ...],
        data_classification: str,
        network_profile: ScientificSkillNetworkProfile,
        allowed_network_targets: tuple[str, ...],
        approval_level: ScientificSkillApprovalLevel,
        approval_receipt_digest: str | None,
    ) -> "ScientificSkillCatalogEntry":
        identity = {
            "skill_name": skill_name,
            "upstream_repository": AUTHORIZED_UPSTREAM_REPOSITORY,
            "upstream_path": upstream_path,
            "upstream_pin": upstream_pin,
            "skill_sha256": skill_sha256,
            "risk_profile_digest": risk_profile_digest,
        }
        entry_id = f"skillentry_{_digest(identity)}"
        entry = cls(
            entry_id=entry_id,
            skill_name=skill_name,
            upstream_repository=AUTHORIZED_UPSTREAM_REPOSITORY,
            upstream_path=upstream_path,
            upstream_pin=upstream_pin,
            skill_sha256=skill_sha256,
            risk_profile_digest=risk_profile_digest,
            status=status,
            allowed_mode=allowed_mode,
            context_budget_tokens=context_budget_tokens,
            allowed_tools=tuple(sorted(allowed_tools)),
            data_classification=data_classification,
            network_profile=network_profile,
            allowed_network_targets=tuple(sorted(allowed_network_targets)),
            approval_level=approval_level,
            approval_receipt_digest=approval_receipt_digest,
        )
        entry._validate_shape()
        return entry

    @classmethod
    def from_mapping(cls, value: object) -> "ScientificSkillCatalogEntry":
        fields = frozenset(
            {
                "entry_id",
                "skill_name",
                "upstream_repository",
                "upstream_path",
                "upstream_pin",
                "skill_sha256",
                "risk_profile_digest",
                "status",
                "allowed_mode",
                "context_budget_tokens",
                "allowed_tools",
                "data_classification",
                "network_profile",
                "allowed_network_targets",
                "approval_level",
                "approval_receipt_digest",
            }
        )
        data = _closed(value, fields)
        try:
            status = ScientificSkillCatalogEntryStatus(data["status"])
            allowed_mode = ScientificSkillOperatingMode(data["allowed_mode"])
            network_profile = ScientificSkillNetworkProfile(data["network_profile"])
            approval_level = ScientificSkillApprovalLevel(data["approval_level"])
        except (TypeError, ValueError) as exc:
            raise ScientificSkillCatalogError("scientific_skill_catalog_enum_invalid") from exc
        if not isinstance(data["allowed_tools"], list) or not isinstance(data["allowed_network_targets"], list):
            raise ScientificSkillCatalogError("scientific_skill_catalog_entry_shape_invalid")
        entry = cls(
            entry_id=data["entry_id"],
            skill_name=data["skill_name"],
            upstream_repository=data["upstream_repository"],
            upstream_path=data["upstream_path"],
            upstream_pin=data["upstream_pin"],
            skill_sha256=data["skill_sha256"],
            risk_profile_digest=data["risk_profile_digest"],
            status=status,
            allowed_mode=allowed_mode,
            context_budget_tokens=data["context_budget_tokens"],
            allowed_tools=tuple(data["allowed_tools"]),
            data_classification=data["data_classification"],
            network_profile=network_profile,
            allowed_network_targets=tuple(data["allowed_network_targets"]),
            approval_level=approval_level,
            approval_receipt_digest=data["approval_receipt_digest"],
        )
        entry._validate_shape()
        expected = cls.create(
            skill_name=entry.skill_name,
            upstream_path=entry.upstream_path,
            upstream_pin=entry.upstream_pin,
            skill_sha256=entry.skill_sha256,
            risk_profile_digest=entry.risk_profile_digest,
            status=entry.status,
            allowed_mode=entry.allowed_mode,
            context_budget_tokens=entry.context_budget_tokens,
            allowed_tools=entry.allowed_tools,
            data_classification=entry.data_classification,
            network_profile=entry.network_profile,
            allowed_network_targets=entry.allowed_network_targets,
            approval_level=entry.approval_level,
            approval_receipt_digest=entry.approval_receipt_digest,
        )
        if entry.entry_id != expected.entry_id:
            raise ScientificSkillCatalogError("scientific_skill_catalog_entry_id_invalid")
        return entry

    def _validate_shape(self) -> None:
        if (
            not isinstance(self.entry_id, str)
            or not self.entry_id.startswith("skillentry_")
            or not _DIGEST.fullmatch(self.entry_id.removeprefix("skillentry_"))
            or not isinstance(self.skill_name, str)
            or not _IDENTIFIER.fullmatch(self.skill_name)
            or self.upstream_repository != AUTHORIZED_UPSTREAM_REPOSITORY
            or not isinstance(self.upstream_path, str)
            or self.upstream_path.startswith(("/", "../"))
            or ".." in self.upstream_path.split("/")
            or not isinstance(self.upstream_pin, str)
            or not _PIN.fullmatch(self.upstream_pin)
            or not isinstance(self.skill_sha256, str)
            or not _DIGEST.fullmatch(self.skill_sha256)
            or not isinstance(self.risk_profile_digest, str)
            or not _DIGEST.fullmatch(self.risk_profile_digest)
        ):
            raise ScientificSkillCatalogError("scientific_skill_catalog_entry_identity_invalid")
        if (
            self.allowed_mode is ScientificSkillOperatingMode.BLOCKED
            or isinstance(self.context_budget_tokens, bool)
            or not isinstance(self.context_budget_tokens, int)
            or not 1 <= self.context_budget_tokens <= 250_000
            or self.data_classification not in _CLASSIFICATION_ORDER
            or len(set(self.allowed_tools)) != len(self.allowed_tools)
            or not set(self.allowed_tools).issubset(_ALLOWED_TOOLS)
            or len(set(self.allowed_network_targets)) != len(self.allowed_network_targets)
            or any(not _network_target(value) for value in self.allowed_network_targets)
        ):
            raise ScientificSkillCatalogError("scientific_skill_catalog_entry_policy_invalid")
        if self.status is ScientificSkillCatalogEntryStatus.APPROVED:
            if not isinstance(self.approval_receipt_digest, str) or not _DIGEST.fullmatch(
                self.approval_receipt_digest
            ):
                raise ScientificSkillCatalogError("scientific_skill_catalog_approval_invalid")
        elif self.approval_receipt_digest is not None:
            raise ScientificSkillCatalogError("scientific_skill_catalog_approval_invalid")

    def to_mapping(self) -> dict[str, object]:
        return {
            "entry_id": self.entry_id,
            "skill_name": self.skill_name,
            "upstream_repository": self.upstream_repository,
            "upstream_path": self.upstream_path,
            "upstream_pin": self.upstream_pin,
            "skill_sha256": self.skill_sha256,
            "risk_profile_digest": self.risk_profile_digest,
            "status": self.status.value,
            "allowed_mode": self.allowed_mode.value,
            "context_budget_tokens": self.context_budget_tokens,
            "allowed_tools": list(self.allowed_tools),
            "data_classification": self.data_classification,
            "network_profile": self.network_profile.value,
            "allowed_network_targets": list(self.allowed_network_targets),
            "approval_level": self.approval_level.value,
            "approval_receipt_digest": self.approval_receipt_digest,
        }


@dataclass(frozen=True)
class ScientificSkillCatalog:
    schema_version: str
    catalog_id: str
    catalog_version: str
    feature_enabled: bool
    entries: tuple[ScientificSkillCatalogEntry, ...]
    catalog_digest: str

    @classmethod
    def create(
        cls,
        *,
        catalog_id: str,
        catalog_version: str,
        feature_enabled: bool,
        entries: tuple[ScientificSkillCatalogEntry, ...],
    ) -> "ScientificSkillCatalog":
        projection = {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "catalog_id": catalog_id,
            "catalog_version": catalog_version,
            "feature_enabled": feature_enabled,
            "entries": [entry.to_mapping() for entry in entries],
        }
        catalog = cls(
            CATALOG_SCHEMA_VERSION,
            catalog_id,
            catalog_version,
            feature_enabled,
            entries,
            _digest(projection),
        )
        catalog._validate()
        return catalog

    @classmethod
    def from_mapping(cls, value: object) -> "ScientificSkillCatalog":
        data = _closed(
            value,
            frozenset(
                {
                    "schema_version",
                    "catalog_id",
                    "catalog_version",
                    "feature_enabled",
                    "entries",
                    "catalog_digest",
                }
            ),
        )
        if data["schema_version"] != CATALOG_SCHEMA_VERSION or not isinstance(data["entries"], list):
            raise ScientificSkillCatalogError("scientific_skill_catalog_shape_invalid")
        entries = tuple(ScientificSkillCatalogEntry.from_mapping(item) for item in data["entries"])
        catalog = cls(
            data["schema_version"],
            data["catalog_id"],
            data["catalog_version"],
            data["feature_enabled"],
            entries,
            data["catalog_digest"],
        )
        catalog._validate()
        expected = cls.create(
            catalog_id=catalog.catalog_id,
            catalog_version=catalog.catalog_version,
            feature_enabled=catalog.feature_enabled,
            entries=catalog.entries,
        )
        if expected.catalog_digest != catalog.catalog_digest:
            raise ScientificSkillCatalogError("scientific_skill_catalog_digest_invalid")
        return catalog

    def _validate(self) -> None:
        if (
            self.schema_version != CATALOG_SCHEMA_VERSION
            or not isinstance(self.catalog_id, str)
            or not _IDENTIFIER.fullmatch(self.catalog_id)
            or not isinstance(self.catalog_version, str)
            or not _IDENTIFIER.fullmatch(self.catalog_version)
            or not isinstance(self.feature_enabled, bool)
            or not isinstance(self.catalog_digest, str)
            or not _DIGEST.fullmatch(self.catalog_digest)
            or len({entry.entry_id for entry in self.entries}) != len(self.entries)
        ):
            raise ScientificSkillCatalogError("scientific_skill_catalog_shape_invalid")
        approved_names = [
            entry.skill_name
            for entry in self.entries
            if entry.status is ScientificSkillCatalogEntryStatus.APPROVED
        ]
        if len(set(approved_names)) != len(approved_names):
            raise ScientificSkillCatalogError("scientific_skill_catalog_approved_entry_ambiguous")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "catalog_id": self.catalog_id,
            "catalog_version": self.catalog_version,
            "feature_enabled": self.feature_enabled,
            "entries": [entry.to_mapping() for entry in self.entries],
            "catalog_digest": self.catalog_digest,
        }


class ScientificSkillCatalogStorePort(Protocol):
    def latest(self, *, catalog_id: str) -> ScientificSkillCatalog | None: ...

    def get(self, *, catalog_id: str, catalog_version: str) -> ScientificSkillCatalog | None: ...

    def append(self, catalog: ScientificSkillCatalog) -> ScientificSkillCatalog: ...


class ScientificSkillCatalogService:
    def __init__(self, store: ScientificSkillCatalogStorePort) -> None:
        self._store = store

    @staticmethod
    def validate_binding(
        entry: ScientificSkillCatalogEntry,
        *,
        manifest: ScientificSkillManifest,
        profile: ScientificSkillRiskProfile,
    ) -> None:
        if (
            entry.skill_name != manifest.name
            or entry.upstream_path != manifest.upstream_path
            or entry.upstream_pin != manifest.upstream_pin
            or entry.skill_sha256 != manifest.sha256
            or entry.risk_profile_digest != profile.profile_digest
            or profile.skill_sha256 != manifest.sha256
        ):
            raise ScientificSkillCatalogError("scientific_skill_catalog_binding_mismatch")
        if profile.operating_mode is ScientificSkillOperatingMode.BLOCKED or (
            _MODE_ORDER[entry.allowed_mode] > _MODE_ORDER[profile.operating_mode]
        ):
            raise ScientificSkillCatalogError("scientific_skill_catalog_mode_not_admitted")
        if entry.context_budget_tokens > profile.context_budget_tokens:
            raise ScientificSkillCatalogError("scientific_skill_catalog_context_budget_invalid")
        if _CLASSIFICATION_ORDER[entry.data_classification] < _CLASSIFICATION_ORDER[profile.data_classification]:
            raise ScientificSkillCatalogError("scientific_skill_catalog_data_classification_invalid")
        if not set(entry.allowed_network_targets).issubset(profile.network_targets):
            raise ScientificSkillCatalogError("scientific_skill_catalog_network_target_invalid")
        ScientificSkillCatalogService._validate_mode_policy(entry, profile)

    @staticmethod
    def _validate_mode_policy(
        entry: ScientificSkillCatalogEntry,
        profile: ScientificSkillRiskProfile,
    ) -> None:
        if entry.allowed_mode is ScientificSkillOperatingMode.DOCUMENTATION_ONLY:
            if entry.allowed_tools or entry.network_profile is not ScientificSkillNetworkProfile.DENIED:
                raise ScientificSkillCatalogError("scientific_skill_catalog_documentation_policy_invalid")
        elif entry.allowed_mode is ScientificSkillOperatingMode.READ_ONLY_RESEARCH:
            if (
                not set(entry.allowed_tools).issubset({"source_lookup", "artifact_read", "citation_search"})
                or entry.approval_level not in {
                    ScientificSkillApprovalLevel.NONE,
                    ScientificSkillApprovalLevel.SOURCE_ACCESS,
                }
                or entry.network_profile is ScientificSkillNetworkProfile.APPROVAL_REQUIRED
            ):
                raise ScientificSkillCatalogError("scientific_skill_catalog_research_policy_invalid")
        elif (
            entry.approval_level is not ScientificSkillApprovalLevel.TASK
            or entry.network_profile is not ScientificSkillNetworkProfile.APPROVAL_REQUIRED
            or set(entry.allowed_tools) != {"sandbox_task_request"}
        ):
            raise ScientificSkillCatalogError("scientific_skill_catalog_execution_policy_invalid")
        if (
            entry.allowed_mode is not ScientificSkillOperatingMode.DOCUMENTATION_ONLY
            and "network" in profile.detected_capabilities
            and not entry.allowed_network_targets
        ):
            raise ScientificSkillCatalogError("scientific_skill_catalog_network_target_invalid")

    def publish(
        self,
        catalog: ScientificSkillCatalog,
        *,
        bindings: Mapping[str, tuple[ScientificSkillManifest, ScientificSkillRiskProfile]],
    ) -> ScientificSkillCatalog:
        if set(bindings) != {entry.entry_id for entry in catalog.entries}:
            raise ScientificSkillCatalogError("scientific_skill_catalog_binding_set_mismatch")
        for entry in catalog.entries:
            manifest, profile = bindings[entry.entry_id]
            self.validate_binding(entry, manifest=manifest, profile=profile)
        existing = self._store.get(catalog_id=catalog.catalog_id, catalog_version=catalog.catalog_version)
        if existing is not None:
            if existing.catalog_digest != catalog.catalog_digest:
                raise ScientificSkillCatalogError("scientific_skill_catalog_version_conflict")
            return existing
        previous = self._store.latest(catalog_id=catalog.catalog_id)
        if previous is not None:
            current_approved = {
                entry.entry_id: entry
                for entry in catalog.entries
                if entry.status is ScientificSkillCatalogEntryStatus.APPROVED
            }
            for old_entry in previous.entries:
                if (
                    old_entry.status is ScientificSkillCatalogEntryStatus.APPROVED
                    and current_approved.get(old_entry.entry_id) != old_entry
                ):
                    raise ScientificSkillCatalogError("scientific_skill_catalog_approved_pin_overwrite_denied")
        return self._store.append(catalog)

    @staticmethod
    def resolve(catalog: ScientificSkillCatalog, *, skill_name: str) -> ScientificSkillCatalogEntry:
        if not catalog.feature_enabled:
            raise ScientificSkillCatalogError("scientific_skill_catalog_feature_disabled")
        matches = tuple(
            entry
            for entry in catalog.entries
            if entry.skill_name == skill_name and entry.status is ScientificSkillCatalogEntryStatus.APPROVED
        )
        if len(matches) != 1:
            raise ScientificSkillCatalogError("scientific_skill_catalog_entry_not_admitted")
        return matches[0]


def _closed(value: object, fields: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields or any(not isinstance(key, str) for key in value):
        raise ScientificSkillCatalogError("scientific_skill_catalog_shape_invalid")
    return value


def _network_target(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 253
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value) is not None
    )


def _digest(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ScientificSkillCatalogError("scientific_skill_catalog_not_canonical") from exc
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "ScientificSkillApprovalLevel",
    "ScientificSkillCatalog",
    "ScientificSkillCatalogEntry",
    "ScientificSkillCatalogEntryStatus",
    "ScientificSkillCatalogError",
    "ScientificSkillCatalogService",
    "ScientificSkillCatalogStorePort",
    "ScientificSkillNetworkProfile",
]
