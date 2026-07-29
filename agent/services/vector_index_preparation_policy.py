"""Hub-owned authorization policy for vector-index embedding preparation."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from worker.retrieval.vector_index_embedding_egress_policy import (
    VECTOR_INDEX_EMBEDDING_POLICY_FORBIDDEN,
)
from worker.retrieval.vector_index_preparation import (
    CODECOMPASS_DOCUMENTS,
    VECTOR_INDEX_PREPARATION_SCHEMA,
    VectorIndexPreparationSpec,
)

HUB_EMBEDDING_POLICY_PROFILES_ENV = "ANANTA_VECTOR_INDEX_EMBEDDING_PROFILES_JSON"
_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DOMAINS = frozenset({"codecompass", "wiki"})


class VectorIndexPreparationPolicyPort(Protocol):
    """Narrow Hub capability used before a task can be persisted."""

    def authorize(
        self,
        *,
        preparation: Mapping[str, Any] | None,
        trusted_domain: str,
    ) -> dict[str, Any] | None: ...


class VectorIndexPreparationPolicyError(ValueError):
    """Stable caller-facing denial without policy-detail disclosure."""

    def __init__(self) -> None:
        super().__init__(VECTOR_INDEX_EMBEDDING_POLICY_FORBIDDEN)


class VectorIndexPreparationPolicyConfigurationError(ValueError):
    """Deployment configuration is malformed and cannot grant egress."""


@dataclass(frozen=True, slots=True)
class DeploymentEmbeddingProfile:
    profile_id: str
    domains: frozenset[str]
    embedding: Mapping[str, Any]


class DeploymentVectorIndexPreparationPolicy:
    """Immutable exact-match profiles captured at Hub process startup."""

    def __init__(
        self,
        profiles: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        normalized = {profile_id: self._profile(profile_id, raw) for profile_id, raw in dict(profiles or {}).items()}
        self._profiles = MappingProxyType(normalized)

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "DeploymentVectorIndexPreparationPolicy":
        source = environ if environ is not None else os.environ
        raw = str(source.get(HUB_EMBEDDING_POLICY_PROFILES_ENV) or "").strip()
        if not raw:
            return cls()
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise VectorIndexPreparationPolicyConfigurationError(
                "vector_index_embedding_policy_profiles_json_invalid"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise VectorIndexPreparationPolicyConfigurationError(
                "vector_index_embedding_policy_profiles_mapping_required"
            )
        return cls(decoded)

    def authorize(
        self,
        *,
        preparation: Mapping[str, Any] | None,
        trusted_domain: str,
    ) -> dict[str, Any] | None:
        if preparation is None:
            return None
        raw_embedding = preparation.get("embedding")
        requested_provider = (
            str(raw_embedding.get("provider") or "").strip().lower() if isinstance(raw_embedding, Mapping) else ""
        )
        try:
            spec = VectorIndexPreparationSpec.from_mapping(preparation)
        except ValueError as exc:
            if requested_provider in {"openai", "openai_compatible"}:
                raise VectorIndexPreparationPolicyError() from exc
            raise
        embedding = dict(spec.embedding)
        if embedding.get("provider") == "local_hash":
            return spec.to_dict()
        profile_id = str(embedding.get("policy_profile") or "").strip()
        profile = self._profiles.get(profile_id)
        domain = str(trusted_domain or "").strip().lower()
        if profile is None or domain not in profile.domains or embedding != dict(profile.embedding):
            raise VectorIndexPreparationPolicyError()
        return VectorIndexPreparationSpec(
            kind=spec.kind,
            embedding=dict(profile.embedding),
            embedding_text_profile=spec.embedding_text_profile,
            retrieval_cache_state=spec.retrieval_cache_state,
        ).to_dict()

    @staticmethod
    def _profile(
        profile_id: str,
        raw: Mapping[str, Any],
    ) -> DeploymentEmbeddingProfile:
        normalized_id = str(profile_id or "").strip()
        if normalized_id != profile_id or _PROFILE_ID.fullmatch(normalized_id) is None or not isinstance(raw, Mapping):
            raise VectorIndexPreparationPolicyConfigurationError("vector_index_embedding_policy_profile_invalid")
        payload = dict(raw)
        if set(payload) != {"domains", "embedding"}:
            raise VectorIndexPreparationPolicyConfigurationError("vector_index_embedding_policy_profile_fields_invalid")
        raw_domains = payload.get("domains")
        if (
            not isinstance(raw_domains, list)
            or not raw_domains
            or any(not isinstance(item, str) for item in raw_domains)
        ):
            raise VectorIndexPreparationPolicyConfigurationError(
                "vector_index_embedding_policy_profile_domains_invalid"
            )
        domains = [item.strip().lower() for item in raw_domains]
        if domains != sorted(set(domains)) or not set(domains).issubset(_DOMAINS):
            raise VectorIndexPreparationPolicyConfigurationError(
                "vector_index_embedding_policy_profile_domains_invalid"
            )
        raw_embedding = payload.get("embedding")
        if not isinstance(raw_embedding, Mapping):
            raise VectorIndexPreparationPolicyConfigurationError(
                "vector_index_embedding_policy_profile_embedding_invalid"
            )
        embedding = dict(raw_embedding)
        if "policy_profile" in embedding:
            raise VectorIndexPreparationPolicyConfigurationError(
                "vector_index_embedding_policy_profile_embedding_invalid"
            )
        try:
            normalized = VectorIndexPreparationSpec.from_mapping(
                {
                    "schema": VECTOR_INDEX_PREPARATION_SCHEMA,
                    "kind": CODECOMPASS_DOCUMENTS,
                    "embedding": {
                        **embedding,
                        "policy_profile": normalized_id,
                    },
                    "embedding_text_profile": ("codecompass-symbol-path-summary-v1"),
                }
            )
        except ValueError as exc:
            raise VectorIndexPreparationPolicyConfigurationError(
                "vector_index_embedding_policy_profile_embedding_invalid"
            ) from exc
        canonical_embedding = dict(normalized.embedding)
        if canonical_embedding.get("provider") != "openai_compatible":
            raise VectorIndexPreparationPolicyConfigurationError(
                "vector_index_embedding_policy_profile_external_required"
            )
        comparable = dict(canonical_embedding)
        comparable.pop("policy_profile", None)
        if comparable != embedding:
            raise VectorIndexPreparationPolicyConfigurationError("vector_index_embedding_policy_profile_not_normalized")
        return DeploymentEmbeddingProfile(
            profile_id=normalized_id,
            domains=frozenset(domains),
            embedding=MappingProxyType(canonical_embedding),
        )


def build_vector_index_preparation_policy(
    environ: Mapping[str, str] | None = None,
) -> DeploymentVectorIndexPreparationPolicy:
    """Build a fail-closed immutable policy from deployment configuration."""

    try:
        return DeploymentVectorIndexPreparationPolicy.from_environment(environ)
    except VectorIndexPreparationPolicyConfigurationError:
        return DeploymentVectorIndexPreparationPolicy()


__all__ = [
    "HUB_EMBEDDING_POLICY_PROFILES_ENV",
    "DeploymentEmbeddingProfile",
    "DeploymentVectorIndexPreparationPolicy",
    "VectorIndexPreparationPolicyConfigurationError",
    "VectorIndexPreparationPolicyError",
    "VectorIndexPreparationPolicyPort",
    "build_vector_index_preparation_policy",
]
