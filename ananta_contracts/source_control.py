"""Versioned, framework-neutral source-control and access contracts.

These immutable wire models contain no persistence, route, queue, or worker
dependencies. The Hub resolves every authoritative identity and policy
binding. Workers receive only a :class:`DelegatedSourceManifestRef`, never a
connection configuration, credential, mutable policy, or browser claim.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Mapping

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

SOURCE_CONNECTION_SCHEMA = "ananta.source-control.source-connection.v1"
SOURCE_REVISION_SCHEMA = "ananta.source-control.source-revision.v1"
SOURCE_REF_MAPPING_SCHEMA = "ananta.source-control.source-ref-mapping.v1"
DESTINATION_DESCRIPTOR_SCHEMA = (
    "ananta.source-control.destination-descriptor.v1"
)
SOURCE_ACCESS_GRANT_SCHEMA = "ananta.source-control.source-access-grant.v1"
DELEGATED_SOURCE_MANIFEST_REF_SCHEMA = (
    "ananta.source-control.delegated-source-manifest-ref.v1"
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONNECTION_ID_RE = re.compile(r"^conn_[0-9a-f]{64}$")
_REVISION_ID_RE = re.compile(r"^srev_[0-9a-f]{64}$")
_SOURCE_REF_ID_RE = re.compile(r"^sref_[0-9a-f]{64}$")
_DESTINATION_ID_RE = re.compile(r"^dst_[0-9a-f]{64}$")
_GRANT_ID_RE = re.compile(r"^grant_[0-9a-f]{64}$")
_MANIFEST_ID_RE = re.compile(r"^manifest_[0-9a-f]{64}$")

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    ),
]
Purpose = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.:-]{0,127}$",
    ),
]
RevisionToken = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.+@:/-]{0,255}$",
    ),
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ConnectionId = Annotated[
    str, StringConstraints(pattern=r"^conn_[0-9a-f]{64}$")
]
SourceRevisionId = Annotated[
    str, StringConstraints(pattern=r"^srev_[0-9a-f]{64}$")
]
SourceRefId = Annotated[
    str, StringConstraints(pattern=r"^sref_[0-9a-f]{64}$")
]
DestinationId = Annotated[
    str, StringConstraints(pattern=r"^dst_[0-9a-f]{64}$")
]
GrantId = Annotated[
    str, StringConstraints(pattern=r"^grant_[0-9a-f]{64}$")
]
ManifestId = Annotated[
    str, StringConstraints(pattern=r"^manifest_[0-9a-f]{64}$")
]

ContractKind = Literal[
    "source_connection",
    "source_revision",
    "source_ref_mapping",
    "destination_descriptor",
    "source_access_grant",
    "delegated_source_manifest_ref",
]


class ConnectorType(str, Enum):
    REGISTERED_WORKSPACE = "registered_workspace"
    LOCAL_DIRECTORY = "local_directory"
    GIT = "git"
    GITHUB = "github"
    KEYCLOAK_DOCS = "keycloak_docs"
    WIKIMEDIA_DUMP = "wikimedia_dump"
    WEB_DOC = "web_doc"
    LOCAL_DUMP = "local_dump"
    OPEN_NOTEBOOK = "open_notebook"
    WIKI = "wiki"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    INTERNAL_HIGH = "internal_high"
    SECRET = "secret"
    CREDENTIAL = "credential"
    SECURITY_SENSITIVE = "security_sensitive"


class ConnectionState(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    TOMBSTONED = "tombstoned"


class AdmissionState(str, Enum):
    PENDING = "pending"
    ADMITTED = "admitted"
    BLOCKED = "blocked"


class ProviderLocation(str, Enum):
    LOCAL_CONTAINER = "local_container"
    PRIVATE_NETWORK = "private_network"
    TENANT_REGION = "tenant_region"
    EXTERNAL_REGION = "external_region"


class GrantOperation(str, Enum):
    INVENTORY = "inventory"
    INDEX = "index"
    RETRIEVE = "retrieve"
    ANALYZE = "analyze"
    SUMMARIZE = "summarize"
    CHAT_CONTEXT = "chat_context"
    TOOL_CONTEXT = "tool_context"
    EXPORT = "export"


class GrantTransformation(str, Enum):
    RAW = "raw"
    REDACTED = "redacted"
    SUMMARY = "summary"


class GrantState(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"


class SourceControlReasonCode(str, Enum):
    CONTRACT_INVALID = "source_control_contract_invalid"
    IDENTITY_MISMATCH = "source_control_identity_mismatch"


class SourceControlContractError(ValueError):
    """Stable boundary error raised without leaking validation internals."""

    def __init__(self, reason_code: SourceControlReasonCode) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code.value)


def _derived_id(prefix: str, coordinates: Mapping[str, object]) -> str:
    canonical = json.dumps(
        dict(coordinates),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"{prefix}_{hashlib.sha256(canonical).hexdigest()}"


def _enum_value(value: str | Enum) -> str:
    return str(value.value if isinstance(value, Enum) else value)


def _wire_timestamp(value: datetime | str) -> str:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("+00:00"):
            return f"{normalized[:-6]}Z"
        return normalized
    normalized = value.isoformat()
    return normalized.replace("+00:00", "Z")


def derive_source_connection_id(
    *,
    tenant_id: str,
    project_id: str,
    connector_type: str | ConnectorType,
    connection_identity_digest: str,
) -> str:
    """Derive the Hub-owned connection ID from non-secret identity material."""

    return _derived_id(
        "conn",
        {
            "connector_type": _enum_value(connector_type),
            "identity_digest": connection_identity_digest,
            "project_id": project_id,
            "tenant_id": tenant_id,
        },
    )


def derive_source_revision_id(
    *, connection_id: str, revision_digest: str
) -> str:
    return _derived_id(
        "srev",
        {
            "connection_id": connection_id,
            "revision_digest": revision_digest,
        },
    )


def derive_source_ref_id(
    *, source_revision_id: str, provenance_digest: str
) -> str:
    return _derived_id(
        "sref",
        {
            "provenance_digest": provenance_digest,
            "source_revision_id": source_revision_id,
        },
    )


def derive_destination_id(
    *,
    worker_id: str,
    worker_kind: str,
    runtime_id: str,
    runtime_kind: str,
    provider_id: str,
    model_id: str,
    model_class: str,
    provider_location: str | ProviderLocation,
    data_residency: str,
) -> str:
    """Bind the complete server-resolved execution destination identity."""

    return _derived_id(
        "dst",
        {
            "data_residency": data_residency,
            "model_class": model_class,
            "model_id": model_id,
            "provider_id": provider_id,
            "provider_location": _enum_value(provider_location),
            "runtime_id": runtime_id,
            "runtime_kind": runtime_kind,
            "worker_id": worker_id,
            "worker_kind": worker_kind,
        },
    )


def derive_source_access_grant_id(
    *,
    tenant_id: str,
    project_id: str,
    source_revision_id: str,
    destination_id: str,
    operation: str | GrantOperation,
    transformation: str | GrantTransformation,
    purpose: str,
    expires_at: datetime | str,
    policy_version: str,
    version: int,
) -> str:
    """Derive a grant-version identity from every authorization binding."""

    return _derived_id(
        "grant",
        {
            "destination_id": destination_id,
            "expires_at": _wire_timestamp(expires_at),
            "operation": _enum_value(operation),
            "policy_version": policy_version,
            "project_id": project_id,
            "purpose": purpose,
            "source_revision_id": source_revision_id,
            "tenant_id": tenant_id,
            "transformation": _enum_value(transformation),
            "version": version,
        },
    )


class _ClosedHubContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    authority: Literal["hub"]

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


class SourceConnection(_ClosedHubContract):
    schema_version: Literal[
        "ananta.source-control.source-connection.v1"
    ] = Field(validation_alias="schema", serialization_alias="schema")
    connection_id: ConnectionId
    tenant_id: Identifier
    project_id: Identifier
    owner_id: Identifier
    connector_type: ConnectorType
    connection_identity_digest: Sha256
    display_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    sensitivity: Sensitivity
    state: ConnectionState
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _identity_matches_coordinates(self) -> "SourceConnection":
        expected = derive_source_connection_id(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            connector_type=self.connector_type,
            connection_identity_digest=self.connection_identity_digest,
        )
        if self.connection_id != expected:
            raise ValueError(SourceControlReasonCode.IDENTITY_MISMATCH.value)
        return self

    @classmethod
    def create(cls, **values: Any) -> "SourceConnection":
        values = dict(values)
        values["schema"] = SOURCE_CONNECTION_SCHEMA
        values["authority"] = "hub"
        values["connection_id"] = derive_source_connection_id(
            tenant_id=values["tenant_id"],
            project_id=values["project_id"],
            connector_type=values["connector_type"],
            connection_identity_digest=values["connection_identity_digest"],
        )
        return cls.model_validate(values)


class SourceRevision(_ClosedHubContract):
    schema_version: Literal[
        "ananta.source-control.source-revision.v1"
    ] = Field(validation_alias="schema", serialization_alias="schema")
    source_revision_id: SourceRevisionId
    connection_id: ConnectionId
    tenant_id: Identifier
    project_id: Identifier
    owner_id: Identifier
    connector_type: ConnectorType
    sensitivity: Sensitivity
    revision_token: RevisionToken
    revision_digest: Sha256
    content_manifest_id: ManifestId
    content_manifest_digest: Sha256
    admission_state: AdmissionState
    captured_at: AwareDatetime

    @model_validator(mode="after")
    def _identity_matches_coordinates(self) -> "SourceRevision":
        expected = derive_source_revision_id(
            connection_id=self.connection_id,
            revision_digest=self.revision_digest,
        )
        if self.source_revision_id != expected:
            raise ValueError(SourceControlReasonCode.IDENTITY_MISMATCH.value)
        return self

    @classmethod
    def create(cls, **values: Any) -> "SourceRevision":
        values = dict(values)
        values["schema"] = SOURCE_REVISION_SCHEMA
        values["authority"] = "hub"
        values["source_revision_id"] = derive_source_revision_id(
            connection_id=values["connection_id"],
            revision_digest=values["revision_digest"],
        )
        return cls.model_validate(values)


class SourceRefMapping(_ClosedHubContract):
    schema_version: Literal[
        "ananta.source-control.source-ref-mapping.v1"
    ] = Field(validation_alias="schema", serialization_alias="schema")
    source_ref_id: SourceRefId
    connection_id: ConnectionId
    source_revision_id: SourceRevisionId
    tenant_id: Identifier
    project_id: Identifier
    provenance_digest: Sha256

    @model_validator(mode="after")
    def _identity_matches_coordinates(self) -> "SourceRefMapping":
        expected = derive_source_ref_id(
            source_revision_id=self.source_revision_id,
            provenance_digest=self.provenance_digest,
        )
        if self.source_ref_id != expected:
            raise ValueError(SourceControlReasonCode.IDENTITY_MISMATCH.value)
        if len({self.source_ref_id, self.connection_id, self.source_revision_id}) != 3:
            raise ValueError(SourceControlReasonCode.IDENTITY_MISMATCH.value)
        return self

    @classmethod
    def create(cls, **values: Any) -> "SourceRefMapping":
        values = dict(values)
        values["schema"] = SOURCE_REF_MAPPING_SCHEMA
        values["authority"] = "hub"
        values["source_ref_id"] = derive_source_ref_id(
            source_revision_id=values["source_revision_id"],
            provenance_digest=values["provenance_digest"],
        )
        return cls.model_validate(values)


class DestinationDescriptor(_ClosedHubContract):
    schema_version: Literal[
        "ananta.source-control.destination-descriptor.v1"
    ] = Field(validation_alias="schema", serialization_alias="schema")
    destination_id: DestinationId
    worker_id: Identifier
    worker_kind: Identifier
    runtime_id: Identifier
    runtime_kind: Identifier
    provider_id: Identifier
    model_id: Identifier
    model_class: Identifier
    provider_location: ProviderLocation
    data_residency: Identifier

    @model_validator(mode="after")
    def _identity_matches_coordinates(self) -> "DestinationDescriptor":
        expected = derive_destination_id(
            worker_id=self.worker_id,
            worker_kind=self.worker_kind,
            runtime_id=self.runtime_id,
            runtime_kind=self.runtime_kind,
            provider_id=self.provider_id,
            model_id=self.model_id,
            model_class=self.model_class,
            provider_location=self.provider_location,
            data_residency=self.data_residency,
        )
        if self.destination_id != expected:
            raise ValueError(SourceControlReasonCode.IDENTITY_MISMATCH.value)
        return self

    @classmethod
    def create(cls, **values: Any) -> "DestinationDescriptor":
        values = dict(values)
        values["schema"] = DESTINATION_DESCRIPTOR_SCHEMA
        values["authority"] = "hub"
        values["destination_id"] = derive_destination_id(
            worker_id=values["worker_id"],
            worker_kind=values["worker_kind"],
            runtime_id=values["runtime_id"],
            runtime_kind=values["runtime_kind"],
            provider_id=values["provider_id"],
            model_id=values["model_id"],
            model_class=values["model_class"],
            provider_location=values["provider_location"],
            data_residency=values["data_residency"],
        )
        return cls.model_validate(values)


class SourceAccessGrant(_ClosedHubContract):
    schema_version: Literal[
        "ananta.source-control.source-access-grant.v1"
    ] = Field(validation_alias="schema", serialization_alias="schema")
    grant_id: GrantId
    version: Annotated[int, Field(ge=1)]
    tenant_id: Identifier
    project_id: Identifier
    source_revision_id: SourceRevisionId
    destination_id: DestinationId
    operation: GrantOperation
    transformation: GrantTransformation
    purpose: Purpose
    policy_version: Identifier
    policy_snapshot_digest: Sha256 | None = None
    state: GrantState
    issued_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def _binding_is_valid(self) -> "SourceAccessGrant":
        expected = derive_source_access_grant_id(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            source_revision_id=self.source_revision_id,
            destination_id=self.destination_id,
            operation=self.operation,
            transformation=self.transformation,
            purpose=self.purpose,
            expires_at=self.expires_at,
            policy_version=self.policy_version,
            version=self.version,
        )
        if self.grant_id != expected or self.expires_at <= self.issued_at:
            raise ValueError(SourceControlReasonCode.IDENTITY_MISMATCH.value)
        return self

    @classmethod
    def create(cls, **values: Any) -> "SourceAccessGrant":
        values = dict(values)
        values["schema"] = SOURCE_ACCESS_GRANT_SCHEMA
        values["authority"] = "hub"
        values["grant_id"] = derive_source_access_grant_id(
            tenant_id=values["tenant_id"],
            project_id=values["project_id"],
            source_revision_id=values["source_revision_id"],
            destination_id=values["destination_id"],
            operation=values["operation"],
            transformation=values["transformation"],
            purpose=values["purpose"],
            expires_at=values["expires_at"],
            policy_version=values["policy_version"],
            version=values["version"],
        )
        return cls.model_validate(values)


class DelegatedSourceManifestRef(_ClosedHubContract):
    """The complete source-control payload a delegated worker may receive."""

    schema_version: Literal[
        "ananta.source-control.delegated-source-manifest-ref.v1"
    ] = Field(validation_alias="schema", serialization_alias="schema")
    manifest_id: ManifestId
    manifest_digest: Sha256
    source_revision_id: SourceRevisionId
    destination_id: DestinationId
    source_access_grant_id: GrantId
    policy_version: Identifier


_CONTRACT_TYPES: dict[str, type[_ClosedHubContract]] = {
    "source_connection": SourceConnection,
    "source_revision": SourceRevision,
    "source_ref_mapping": SourceRefMapping,
    "destination_descriptor": DestinationDescriptor,
    "source_access_grant": SourceAccessGrant,
    "delegated_source_manifest_ref": DelegatedSourceManifestRef,
}


def parse_source_control_contract(
    kind: ContractKind | str,
    payload: Mapping[str, Any],
) -> _ClosedHubContract:
    """Parse an untrusted wire payload into one closed immutable contract."""

    contract_type = _CONTRACT_TYPES.get(str(kind))
    if contract_type is None:
        raise SourceControlContractError(
            SourceControlReasonCode.CONTRACT_INVALID
        )
    try:
        return contract_type.model_validate(dict(payload))
    except (TypeError, ValueError, ValidationError) as exc:
        reason = (
            SourceControlReasonCode.IDENTITY_MISMATCH
            if SourceControlReasonCode.IDENTITY_MISMATCH.value in str(exc)
            else SourceControlReasonCode.CONTRACT_INVALID
        )
        raise SourceControlContractError(reason) from None


__all__ = [
    "DELEGATED_SOURCE_MANIFEST_REF_SCHEMA",
    "DESTINATION_DESCRIPTOR_SCHEMA",
    "SOURCE_ACCESS_GRANT_SCHEMA",
    "SOURCE_CONNECTION_SCHEMA",
    "SOURCE_REF_MAPPING_SCHEMA",
    "SOURCE_REVISION_SCHEMA",
    "AdmissionState",
    "ConnectionState",
    "ConnectorType",
    "DelegatedSourceManifestRef",
    "DestinationDescriptor",
    "GrantOperation",
    "GrantState",
    "GrantTransformation",
    "ProviderLocation",
    "Sensitivity",
    "SourceAccessGrant",
    "SourceConnection",
    "SourceControlContractError",
    "SourceControlReasonCode",
    "SourceRefMapping",
    "SourceRevision",
    "derive_destination_id",
    "derive_source_access_grant_id",
    "derive_source_connection_id",
    "derive_source_ref_id",
    "derive_source_revision_id",
    "parse_source_control_contract",
]
