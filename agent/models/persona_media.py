"""Closed presentation contracts; persona metadata never grants permissions."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,160}$")]
Revision = Annotated[StrictInt, Field(ge=1, lt=2**53)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MediaKind = Literal["image", "voice", "video", "style"]
OwnerKind = Literal["organization", "team", "agent"]
Usage = Literal["preview", "publish", "voice_clone", "face_animation"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MediaAssetRef(ClosedModel):
    tenant_id: Identifier
    project_id: Identifier
    artifact_id: Identifier
    revision: Revision
    sha256: Digest
    kind: MediaKind
    classification: Literal["production", "synthetic", "test_only"]


class MediaSelection(ClosedModel):
    # Missing and explicit inheritance have the same fallback behavior, but
    # remain distinct in the immutable profile and resolution explanation.
    state: Literal["missing", "inherit", "disabled", "asset"] = "missing"
    asset: MediaAssetRef | None = None

    @model_validator(mode="after")
    def validate_selection(self):
        if (self.state == "asset") != (self.asset is not None):
            raise ValueError("persona_selection_asset_mismatch")
        return self


class PersonaMediaProfile(ClosedModel):
    schema_version: Literal["ananta.persona-media.v1"] = "ananta.persona-media.v1"
    tenant_id: Identifier
    project_id: Identifier
    owner_kind: OwnerKind
    owner_id: Identifier
    persona_id: Identifier
    revision: Revision
    image: MediaSelection = Field(default_factory=MediaSelection)
    voice: MediaSelection = Field(default_factory=MediaSelection)
    video: MediaSelection = Field(default_factory=MediaSelection)
    style: MediaSelection = Field(default_factory=MediaSelection)
    requested_usage: tuple[Usage, ...] = ()

    def content_hash(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def validate_assets(self):
        if len(self.requested_usage) != len(set(self.requested_usage)):
            raise ValueError("persona_usage_duplicate")
        for kind in ("image", "voice", "video", "style"):
            asset = getattr(self, kind).asset
            if asset is not None and (
                asset.tenant_id != self.tenant_id or asset.project_id != self.project_id or asset.kind != kind
            ):
                raise ValueError("persona_asset_scope_or_kind_mismatch")
        return self


class PersonaMembership(ClosedModel):
    """Trusted Hub projection, never reconstructed from caller profile fields."""

    tenant_id: Identifier
    project_id: Identifier
    organization_id: Identifier
    team_id: Identifier
    agent_id: Identifier
    assignment_id: Identifier
    membership_revision: Revision


class PersonaProfileSelection(ClosedModel):
    """Configuration pin, not evidence identity, permission or runtime lease."""

    organization_id: Identifier
    owner_kind: OwnerKind
    owner_id: Identifier
    selection_digest: Digest


class SelectionOrigin(ClosedModel):
    owner_kind: OwnerKind
    owner_id: Identifier
    persona_id: Identifier
    profile_revision: Revision
    selection_state: Literal["missing", "inherit", "disabled", "asset"]


class ResolvedMedia(ClosedModel):
    kind: MediaKind
    state: Literal["missing", "disabled", "asset"]
    asset: MediaAssetRef | None
    origins: tuple[SelectionOrigin, ...]


class PersonaMediaResolution(ClosedModel):
    membership: PersonaMembership
    media: tuple[ResolvedMedia, ...]
