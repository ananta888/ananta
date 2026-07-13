"""Portable, immutable Hub decision for one provider invocation path."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

PROVIDER_EXECUTION_BINDING_SCHEMA = "ananta.provider-execution-binding.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$")


class ProviderExecutionBindingError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class ProviderExecutionBinding:
    """Safe provider/model choice made by the Hub, never by a worker."""

    provider_id: str
    model_id: str
    source: str
    reason_code: str
    schema: str = PROVIDER_EXECUTION_BINDING_SCHEMA

    @property
    def binding_id(self) -> str:
        digest = hashlib.sha256(
            "\x00".join(
                (
                    self.schema,
                    self.provider_id,
                    self.model_id,
                    self.source,
                    self.reason_code,
                )
            ).encode("utf-8")
        ).hexdigest()
        return f"provider-binding:{digest}"

    def validate(self) -> None:
        if self.schema != PROVIDER_EXECUTION_BINDING_SCHEMA:
            raise ProviderExecutionBindingError("provider_binding_schema_unsupported")
        for value in (
            self.provider_id,
            self.model_id,
            self.source,
            self.reason_code,
        ):
            if not _IDENTIFIER.fullmatch(str(value or "")):
                raise ProviderExecutionBindingError("provider_binding_invalid")
        if self.model_id.lower() in {"auto", "default", "none", "null"}:
            raise ProviderExecutionBindingError("provider_model_not_resolved")

    @classmethod
    def from_mapping(cls, raw: object) -> "ProviderExecutionBinding":
        if not isinstance(raw, Mapping):
            raise ProviderExecutionBindingError("provider_binding_required")
        value = cls(
            schema=str(raw.get("schema") or "").strip(),
            provider_id=str(raw.get("provider_id") or "").strip().lower(),
            model_id=str(raw.get("model_id") or "").strip(),
            source=str(raw.get("source") or "").strip(),
            reason_code=str(raw.get("reason_code") or "").strip(),
        )
        value.validate()
        supplied_id = str(raw.get("binding_id") or "").strip()
        if supplied_id and supplied_id != value.binding_id:
            raise ProviderExecutionBindingError("provider_binding_id_mismatch")
        return value

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "binding_id": self.binding_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "source": self.source,
            "reason_code": self.reason_code,
        }


__all__ = [
    "PROVIDER_EXECUTION_BINDING_SCHEMA",
    "ProviderExecutionBinding",
    "ProviderExecutionBindingError",
]
