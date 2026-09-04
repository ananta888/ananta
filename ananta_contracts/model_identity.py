"""Closed identity for modified model artifacts used in controlled research."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AbliteratedModelIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["ananta.abliterated-model-identity.v1"] = Field(
        default="ananta.abliterated-model-identity.v1", alias="schema"
    )
    identity_id: str
    base_repository: str
    base_revision: str
    derivative_repository: str
    derivative_revision: str
    artifact_sha256: str
    ablation_line: Literal["unknown", "ud", "ud_dw"]
    ablated_layer_start: int | None = Field(default=None, ge=0, le=512)
    ablated_layer_end: int | None = Field(default=None, ge=0, le=512)
    quantization: str
    runtime_family: str
    trust_class: Literal["unsafe_research"] = "unsafe_research"
    safety_modified: Literal[True] = True

    @field_validator("base_revision", "derivative_revision", "artifact_sha256")
    @classmethod
    def _digest(cls, value: str) -> str:
        expected = 64 if len(value) == 64 else 40
        if len(value) != expected or re.fullmatch(r"[0-9a-f]+", value) is None:
            raise ValueError("model_identity_digest_invalid")
        return value

    @model_validator(mode="after")
    def _layers_match_line(self) -> "AbliteratedModelIdentity":
        layers = (self.ablated_layer_start, self.ablated_layer_end)
        if self.ablation_line == "unknown" and layers != (None, None):
            raise ValueError("unknown_ablation_layers_must_be_empty")
        if self.ablation_line != "unknown" and (layers[0] is None or layers[1] is None or layers[0] > layers[1]):
            raise ValueError("known_ablation_layers_required")
        return self

    def binding_digest(self) -> str:
        payload = self.model_dump(by_alias=True, mode="json")
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = ["AbliteratedModelIdentity"]
