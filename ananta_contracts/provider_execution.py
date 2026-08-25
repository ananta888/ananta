"""Portable, immutable Hub decisions for provider invocation paths."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from ananta_contracts.provider_endpoint_policy import (
    normalize_provider_endpoint_identity,
)

PROVIDER_EXECUTION_BINDING_SCHEMA = "ananta.provider-execution-binding.v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,255}$")
_BINDING_ID = re.compile(r"^provider-binding:[a-f0-9]{64}$")


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
    endpoint_identity: str = ""
    schema: str = PROVIDER_EXECUTION_BINDING_SCHEMA

    @property
    def binding_id(self) -> str:
        values = (
            self.schema,
            self.provider_id,
            self.model_id,
            self.source,
            self.reason_code,
        )
        if self.endpoint_identity:
            values = (*values, self.endpoint_identity)
        digest = hashlib.sha256(
            "\x00".join(values).encode("utf-8")
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
        if self.endpoint_identity:
            try:
                normalized = normalize_provider_endpoint_identity(
                    provider_id=self.provider_id,
                    endpoint_url=self.endpoint_identity,
                )
            except ValueError as exc:
                raise ProviderExecutionBindingError(
                    "provider_endpoint_identity_invalid"
                ) from exc
            if normalized != self.endpoint_identity:
                raise ProviderExecutionBindingError(
                    "provider_endpoint_identity_not_canonical"
                )

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
            endpoint_identity=str(
                raw.get("endpoint_identity") or ""
            ).strip(),
        )
        value.validate()
        supplied_id = str(raw.get("binding_id") or "").strip()
        if supplied_id and supplied_id != value.binding_id:
            raise ProviderExecutionBindingError("provider_binding_id_mismatch")
        return value

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload = {
            "schema": self.schema,
            "binding_id": self.binding_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "source": self.source,
            "reason_code": self.reason_code,
        }
        if self.endpoint_identity:
            payload["endpoint_identity"] = self.endpoint_identity
        return payload


@dataclass(frozen=True)
class ProviderProfileExecutionBinding:
    """Bind one model-routing profile to an exact Hub provider decision."""

    profile_id: str
    binding: ProviderExecutionBinding

    def validate(self) -> None:
        if not _IDENTIFIER.fullmatch(str(self.profile_id or "")):
            raise ProviderExecutionBindingError("provider_profile_id_invalid")
        self.binding.validate()

    @classmethod
    def from_mapping(cls, raw: object) -> "ProviderProfileExecutionBinding":
        if not isinstance(raw, Mapping):
            raise ProviderExecutionBindingError(
                "provider_profile_binding_required"
            )
        value = cls(
            profile_id=str(raw.get("profile_id") or "").strip(),
            binding=ProviderExecutionBinding.from_mapping(raw.get("binding")),
        )
        value.validate()
        return value

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "profile_id": self.profile_id,
            "binding": self.binding.to_dict(),
        }


@dataclass(frozen=True)
class ProviderBindingAuthorization:
    """Minimal canonical provider identity bound into a Hub authorization."""

    binding_id: str
    provider_id: str
    model_id: str
    endpoint_identity: str = ""

    def validate(self) -> None:
        if not _BINDING_ID.fullmatch(str(self.binding_id or "")):
            raise ProviderExecutionBindingError(
                "provider_authorization_binding_id_invalid"
            )
        if (
            not _IDENTIFIER.fullmatch(str(self.provider_id or ""))
            or not _IDENTIFIER.fullmatch(str(self.model_id or ""))
        ):
            raise ProviderExecutionBindingError(
                "provider_authorization_binding_invalid"
            )
        if self.provider_id != self.provider_id.lower():
            raise ProviderExecutionBindingError(
                "provider_authorization_provider_not_canonical"
            )
        if self.endpoint_identity:
            try:
                normalized = normalize_provider_endpoint_identity(
                    provider_id=self.provider_id,
                    endpoint_url=self.endpoint_identity,
                )
            except ValueError as exc:
                raise ProviderExecutionBindingError(
                    "provider_authorization_endpoint_invalid"
                ) from exc
            if normalized != self.endpoint_identity:
                raise ProviderExecutionBindingError(
                    "provider_authorization_endpoint_not_canonical"
                )

    @classmethod
    def from_binding(
        cls,
        binding: ProviderExecutionBinding,
    ) -> "ProviderBindingAuthorization":
        binding.validate()
        value = cls(
            binding_id=binding.binding_id,
            provider_id=binding.provider_id,
            model_id=binding.model_id,
            endpoint_identity=binding.endpoint_identity,
        )
        value.validate()
        return value

    @classmethod
    def from_mapping(cls, raw: object) -> "ProviderBindingAuthorization":
        if not isinstance(raw, Mapping):
            raise ProviderExecutionBindingError(
                "provider_authorization_binding_required"
            )
        value = cls(
            binding_id=str(raw.get("binding_id") or "").strip(),
            provider_id=str(raw.get("provider_id") or "").strip(),
            model_id=str(raw.get("model_id") or "").strip(),
            endpoint_identity=str(
                raw.get("endpoint_identity") or ""
            ).strip(),
        )
        value.validate()
        return value

    def to_dict(self) -> dict[str, str]:
        self.validate()
        payload = {
            "binding_id": self.binding_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
        }
        if self.endpoint_identity:
            payload["endpoint_identity"] = self.endpoint_identity
        return payload


@dataclass(frozen=True)
class ProviderProfileAttemptPlanEntry:
    """One ordered, Hub-authorized profile segment and its exact call cap."""

    profile_id: str
    binding_id: str
    provider_id: str
    model_id: str
    maximum_attempts: int
    endpoint_identity: str = ""
    allowed_error_types: tuple[str, ...] = ()

    @property
    def binding_authorization(self) -> ProviderBindingAuthorization:
        return ProviderBindingAuthorization(
            binding_id=self.binding_id,
            provider_id=self.provider_id,
            model_id=self.model_id,
            endpoint_identity=self.endpoint_identity,
        )

    def validate(self) -> None:
        if not _IDENTIFIER.fullmatch(str(self.profile_id or "")):
            raise ProviderExecutionBindingError(
                "provider_attempt_plan_profile_invalid"
            )
        self.binding_authorization.validate()
        if not 1 <= self.maximum_attempts <= 33:
            raise ProviderExecutionBindingError(
                "provider_attempt_plan_limit_invalid"
            )
        if len(self.allowed_error_types) > 16 or any(
            not _IDENTIFIER.fullmatch(str(value or ""))
            for value in self.allowed_error_types
        ):
            raise ProviderExecutionBindingError(
                "provider_attempt_plan_error_types_invalid"
            )

    @classmethod
    def from_profile_binding(
        cls,
        profile_binding: ProviderProfileExecutionBinding,
        *,
        maximum_attempts: int,
        allowed_error_types: tuple[str, ...] = (),
    ) -> "ProviderProfileAttemptPlanEntry":
        profile_binding.validate()
        value = cls(
            profile_id=profile_binding.profile_id,
            binding_id=profile_binding.binding.binding_id,
            provider_id=profile_binding.binding.provider_id,
            model_id=profile_binding.binding.model_id,
            maximum_attempts=int(maximum_attempts),
            endpoint_identity=profile_binding.binding.endpoint_identity,
            allowed_error_types=tuple(allowed_error_types),
        )
        value.validate()
        return value

    @classmethod
    def from_mapping(cls, raw: object) -> "ProviderProfileAttemptPlanEntry":
        if not isinstance(raw, Mapping):
            raise ProviderExecutionBindingError(
                "provider_attempt_plan_entry_required"
            )
        try:
            value = cls(
                profile_id=str(raw.get("profile_id") or "").strip(),
                binding_id=str(raw.get("binding_id") or "").strip(),
                provider_id=str(raw.get("provider_id") or "").strip(),
                model_id=str(raw.get("model_id") or "").strip(),
                maximum_attempts=int(raw.get("maximum_attempts")),
                endpoint_identity=str(
                    raw.get("endpoint_identity") or ""
                ).strip(),
                allowed_error_types=tuple(
                    str(item or "").strip()
                    for item in (raw.get("allowed_error_types") or ())
                ),
            )
        except (TypeError, ValueError) as exc:
            raise ProviderExecutionBindingError(
                "provider_attempt_plan_entry_invalid"
            ) from exc
        value.validate()
        return value

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload = {
            "profile_id": self.profile_id,
            "binding_id": self.binding_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "maximum_attempts": self.maximum_attempts,
        }
        if self.endpoint_identity:
            payload["endpoint_identity"] = self.endpoint_identity
        if self.allowed_error_types:
            payload["allowed_error_types"] = list(self.allowed_error_types)
        return payload


__all__ = [
    "PROVIDER_EXECUTION_BINDING_SCHEMA",
    "ProviderBindingAuthorization",
    "ProviderExecutionBinding",
    "ProviderExecutionBindingError",
    "ProviderProfileAttemptPlanEntry",
    "ProviderProfileExecutionBinding",
]
