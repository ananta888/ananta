"""Worker-only preparation of vectors from Hub-approved document artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from worker.retrieval.embedding_provider import (
    EmbeddingProvider,
    build_embedding_provider,
)
from worker.retrieval.embedding_text_builder import (
    CODECOMPASS_EMBEDDING_TEXT_PROFILE,
    build_embedding_texts_batch,
)
from worker.retrieval.vector_index_embedding_egress_policy import (
    VectorIndexEmbeddingEgressPolicyError,
    WorkerEmbeddingEgressPolicy,
    normalize_embedding_allowlist,
    normalize_embedding_base_url,
)
from worker.retrieval.vector_store_contract import (
    CompatibilitySpec,
    PreparedVectorPoint,
    VectorScope,
)
from worker.retrieval.vector_store_endpoint_policy import (
    EnvFileSecretResolver,
    SecretReference,
    SecretResolver,
)
from worker.retrieval.wiki_vector_store import (
    WIKI_EMBEDDING_PROFILE,
    WikiVectorPayloadAdapter,
    WikiVectorStoreConfig,
)

VECTOR_INDEX_PREPARATION_SCHEMA = "ananta.vector_index_preparation.v1"
VECTOR_INDEX_DOCUMENT_INPUT_SCHEMA = "ananta.vector_index_documents.v1"
CODECOMPASS_DOCUMENTS = "codecompass_documents"
WIKI_DOCUMENTS = "wiki_documents"
_SUPPORTED_KINDS = frozenset({CODECOMPASS_DOCUMENTS, WIKI_DOCUMENTS})
_KIND_BY_DOMAIN = {
    "codecompass": CODECOMPASS_DOCUMENTS,
    "wiki": WIKI_DOCUMENTS,
}
_PREPARATION_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "embedding",
        "embedding_text_profile",
        "retrieval_cache_state",
    }
)
_EMBEDDING_FIELDS = frozenset(
    {
        "provider",
        "provider_id",
        "policy_profile",
        "model",
        "model_version",
        "dimensions",
        "base_url",
        "api_key_ref",
        "timeout_seconds",
        "external_calls_allowed",
        "allowed_base_urls",
    }
)
_CODECOMPASS_DOCUMENT_FIELDS = frozenset(
    {
        "record_id",
        "kind",
        "file",
        "parent_id",
        "role_labels",
        "importance_score",
        "source_scope",
        "profile_name",
        "manifest_hash",
        "embedding_text",
        "source_hash",
    }
)


@dataclass(frozen=True, slots=True)
class VectorIndexPreparationSpec:
    kind: str
    embedding: Mapping[str, Any]
    embedding_text_profile: str
    retrieval_cache_state: str = ""
    schema: str = VECTOR_INDEX_PREPARATION_SCHEMA

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "VectorIndexPreparationSpec":
        payload = dict(value or {})
        if set(payload) - _PREPARATION_FIELDS:
            raise ValueError("vector_index_preparation_fields_forbidden")
        if payload.get("schema") != VECTOR_INDEX_PREPARATION_SCHEMA:
            raise ValueError("vector_index_preparation_schema_invalid")
        kind = str(payload.get("kind") or "").strip().lower()
        if kind not in _SUPPORTED_KINDS:
            raise ValueError("vector_index_preparation_kind_invalid")
        raw_embedding = payload.get("embedding")
        if not isinstance(raw_embedding, Mapping):
            raise ValueError("vector_index_preparation_embedding_invalid")
        embedding = cls._embedding(dict(raw_embedding))
        expected_profile = (
            CODECOMPASS_EMBEDDING_TEXT_PROFILE if kind == CODECOMPASS_DOCUMENTS else WIKI_EMBEDDING_PROFILE
        )
        profile = str(payload.get("embedding_text_profile") or "").strip()
        if profile != expected_profile:
            raise ValueError("vector_index_preparation_profile_invalid")
        cache_state = str(payload.get("retrieval_cache_state") or "").strip()
        if len(cache_state.encode("utf-8")) > 512 or any(ord(character) < 32 for character in cache_state):
            raise ValueError("vector_index_preparation_cache_state_invalid")
        return cls(
            kind=kind,
            embedding=embedding,
            embedding_text_profile=profile,
            retrieval_cache_state=cache_state,
        )

    @staticmethod
    def _embedding(raw: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(raw or {})
        if set(payload) - _EMBEDDING_FIELDS:
            raise ValueError("vector_index_preparation_embedding_fields_forbidden")
        provider = str(payload.get("provider") or "local_hash").strip().lower()
        if provider in {"local", "hash"}:
            provider = "local_hash"
        elif provider in {"openai", "openai_compatible"}:
            provider = "openai_compatible"
        if provider not in {"local_hash", "openai_compatible"}:
            raise ValueError("vector_index_preparation_embedding_provider_invalid")
        dimensions = payload.get("dimensions")
        if isinstance(dimensions, bool) or not isinstance(dimensions, int):
            raise ValueError("vector_index_preparation_embedding_dimensions_invalid")
        if not 1 <= dimensions <= 65_536:
            raise ValueError("vector_index_preparation_embedding_dimensions_invalid")
        timeout = payload.get("timeout_seconds", 20)
        if isinstance(timeout, bool) or not isinstance(timeout, int):
            raise ValueError("vector_index_preparation_embedding_timeout_invalid")
        if not 1 <= timeout <= 300:
            raise ValueError("vector_index_preparation_embedding_timeout_invalid")
        external_allowed = payload.get("external_calls_allowed", False)
        if not isinstance(external_allowed, bool):
            raise ValueError("vector_index_preparation_embedding_policy_invalid")
        allowed_raw = payload.get("allowed_base_urls") or []
        if not isinstance(allowed_raw, list) or any(not isinstance(item, str) for item in allowed_raw):
            raise ValueError("vector_index_preparation_embedding_allowed_urls_invalid")
        if len(allowed_raw) > 16:
            raise ValueError("vector_index_preparation_embedding_allowed_urls_invalid")
        allowed_urls = [item.strip() for item in allowed_raw]
        if any(
            not item or len(item.encode("utf-8")) > 2048 or any(ord(character) < 32 for character in item)
            for item in allowed_urls
        ):
            raise ValueError("vector_index_preparation_embedding_allowed_urls_invalid")
        base_url = str(payload.get("base_url") or "").strip()
        api_key_ref = str(payload.get("api_key_ref") or "").strip()
        policy_profile = str(payload.get("policy_profile") or "").strip()
        if len(base_url.encode("utf-8")) > 2048 or len(api_key_ref.encode("utf-8")) > 512:
            raise ValueError("vector_index_preparation_embedding_policy_invalid")
        if provider == "local_hash":
            if external_allowed or base_url or api_key_ref or allowed_urls or policy_profile:
                raise ValueError("vector_index_preparation_embedding_policy_invalid")
        else:
            try:
                normalized_base_url = normalize_embedding_base_url(base_url)
                normalized_allowed_urls = normalize_embedding_allowlist(allowed_urls)
            except VectorIndexEmbeddingEgressPolicyError as exc:
                raise ValueError("vector_index_preparation_embedding_policy_invalid") from exc
            if (
                not external_allowed
                or base_url != normalized_base_url
                or not allowed_urls
                or allowed_urls != list(normalized_allowed_urls)
                or base_url not in normalized_allowed_urls
                or not api_key_ref
                or not policy_profile
                or len(policy_profile) > 128
                or any(
                    character not in ("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-")
                    for character in policy_profile
                )
            ):
                raise ValueError("vector_index_preparation_embedding_policy_invalid")
            try:
                SecretReference.parse(api_key_ref)
            except ValueError as exc:
                raise ValueError("vector_index_preparation_embedding_secret_ref_invalid") from exc
        result: dict[str, Any] = {
            "provider": provider,
            **({"policy_profile": policy_profile} if policy_profile else {}),
            "provider_id": str(
                payload.get("provider_id") or ("local_hash" if provider == "local_hash" else "openai_compatible")
            ).strip(),
            "model_version": str(
                payload.get("model_version") or ("hash-v1" if provider == "local_hash" else payload.get("model") or "")
            ).strip(),
            "dimensions": dimensions,
            "timeout_seconds": timeout,
            "external_calls_allowed": external_allowed,
            "allowed_base_urls": allowed_urls,
        }
        if (
            not result["model_version"]
            or len(result["model_version"].encode("utf-8")) > 256
            or any(ord(character) < 32 for character in result["model_version"])
        ):
            raise ValueError("vector_index_preparation_embedding_model_invalid")
        if (
            not result["provider_id"]
            or len(result["provider_id"]) > 128
            or any(ord(character) < 32 for character in result["provider_id"])
        ):
            raise ValueError("vector_index_preparation_embedding_provider_invalid")
        if payload.get("model") is not None:
            model = str(payload.get("model") or "").strip()
            if not model or len(model.encode("utf-8")) > 256 or any(ord(character) < 32 for character in model):
                raise ValueError("vector_index_preparation_embedding_model_invalid")
            result["model"] = model
        if base_url:
            result["base_url"] = base_url
        if api_key_ref:
            result["api_key_ref"] = api_key_ref
        return result

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema": self.schema,
            "kind": self.kind,
            "embedding": dict(self.embedding),
            "embedding_text_profile": self.embedding_text_profile,
        }
        if self.retrieval_cache_state:
            result["retrieval_cache_state"] = self.retrieval_cache_state
        return result

    def validate_scope_domain(self, domain: str) -> None:
        expected = _KIND_BY_DOMAIN.get(str(domain or "").strip().lower())
        if expected is None or self.kind != expected:
            raise ValueError("vector_index_preparation_scope_mismatch")

    def validate_compatibility(
        self,
        compatibility: CompatibilitySpec,
    ) -> None:
        if int(self.embedding["dimensions"]) != compatibility.dimensions:
            raise ValueError("dimensions_mismatch")
        if str(self.embedding["provider_id"]) != compatibility.provider:
            raise ValueError("provider_changed")
        if str(self.embedding["model_version"]) != compatibility.model:
            raise ValueError("model_changed")
        if self.embedding_text_profile != compatibility.profile:
            raise ValueError("profile_changed")


class VectorDocumentPreparer(Protocol):
    kind: str

    def prepare(
        self,
        *,
        documents: Sequence[Mapping[str, Any]],
        scope: VectorScope,
        provider: EmbeddingProvider,
        compatibility: CompatibilitySpec,
        preparation: VectorIndexPreparationSpec,
    ) -> tuple[PreparedVectorPoint, ...]: ...


class CodeCompassDocumentPreparer:
    kind = CODECOMPASS_DOCUMENTS

    def prepare(
        self,
        *,
        documents: Sequence[Mapping[str, Any]],
        scope: VectorScope,
        provider: EmbeddingProvider,
        compatibility: CompatibilitySpec,
        preparation: VectorIndexPreparationSpec,
    ) -> tuple[PreparedVectorPoint, ...]:
        normalized = [self._document(dict(document)) for document in documents]
        vectors = provider.embed_texts(build_embedding_texts_batch(normalized))
        _validate_vectors(vectors, normalized, compatibility)
        return tuple(
            PreparedVectorPoint(
                record_id=document["record_id"],
                vector=tuple(float(item) for item in vector),
                scope=scope,
                source_hash=document["source_hash"],
                payload={
                    "kind": document["kind"],
                    "file": document["file"],
                    "parent_id": document["parent_id"],
                    "role_labels": document["role_labels"],
                    "importance_score": document["importance_score"],
                    "source_scope": document["source_scope"],
                    "profile_name": document["profile_name"],
                    "source_manifest_hash": document["manifest_hash"],
                    "embedding_text": document["embedding_text"],
                },
            )
            for document, vector in zip(
                normalized,
                vectors,
                strict=True,
            )
        )

    @staticmethod
    def _document(raw: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(raw or {})
        if set(payload) - _CODECOMPASS_DOCUMENT_FIELDS:
            raise ValueError("vector_index_codecompass_document_fields_forbidden")
        record_id = _bounded_required(
            payload.get("record_id"),
            "record_id",
            256,
        )
        embedding_text = _bounded_required(
            payload.get("embedding_text"),
            "embedding_text",
            4096,
        )
        role_labels = payload.get("role_labels") or []
        if (
            not isinstance(role_labels, list)
            or len(role_labels) > 64
            or any(not isinstance(item, str) for item in role_labels)
        ):
            raise ValueError("vector_index_codecompass_document_invalid")
        source_hash = str(payload.get("source_hash") or "").strip()
        if not source_hash:
            source_hash = hashlib.sha256(
                json.dumps(
                    {key: value for key, value in payload.items() if key != "source_hash"},
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            ).hexdigest()
        return {
            "record_id": record_id,
            "kind": _bounded_optional(
                payload.get("kind"),
                "unknown",
                128,
            ),
            "file": _bounded_optional(
                payload.get("file"),
                "",
                4096,
            ),
            "parent_id": _bounded_optional(
                payload.get("parent_id"),
                "",
                512,
            ),
            "role_labels": [_bounded_required(item, "role_label", 128) for item in role_labels],
            "importance_score": _bounded_score(payload.get("importance_score")),
            "source_scope": _bounded_optional(
                payload.get("source_scope"),
                "repo",
                128,
            ),
            "profile_name": _bounded_optional(
                payload.get("profile_name"),
                "default",
                256,
            ),
            "manifest_hash": _bounded_optional(
                payload.get("manifest_hash"),
                "",
                256,
            ),
            "embedding_text": embedding_text,
            "source_hash": _bounded_required(
                source_hash,
                "source_hash",
                256,
            ),
        }


class WikiDocumentPreparer:
    kind = WIKI_DOCUMENTS

    def prepare(
        self,
        *,
        documents: Sequence[Mapping[str, Any]],
        scope: VectorScope,
        provider: EmbeddingProvider,
        compatibility: CompatibilitySpec,
        preparation: VectorIndexPreparationSpec,
    ) -> tuple[PreparedVectorPoint, ...]:
        adapter = WikiVectorPayloadAdapter(
            WikiVectorStoreConfig(
                workspace_id=scope.workspace_id,
                source_id=scope.repository_id,
                profile_name=scope.profile_name,
            )
        )
        normalized = tuple(adapter.adapt(dict(document)) for document in documents)
        vectors = provider.embed_texts([document.embedding_text for document in normalized])
        _validate_vectors(vectors, normalized, compatibility)
        return tuple(
            PreparedVectorPoint(
                record_id=document.record_id,
                vector=tuple(float(item) for item in vector),
                scope=scope,
                payload=document.as_store_payload(),
                source_hash=document.source_hash(compatibility.manifest_hash),
            )
            for document, vector in zip(
                normalized,
                vectors,
                strict=True,
            )
        )


class TaskEmbeddingProviderFactory:
    """Resolve only a referenced Worker secret and build one provider."""

    def __init__(
        self,
        *,
        secret_resolver: SecretResolver | None = None,
        egress_policy: WorkerEmbeddingEgressPolicy | None = None,
    ) -> None:
        self._secret_resolver = secret_resolver or EnvFileSecretResolver(
            allowed_env_names=("ANANTA_EMBEDDING_API_KEY",),
        )
        self._egress_policy = (
            egress_policy if egress_policy is not None else WorkerEmbeddingEgressPolicy.from_environment()
        )

    def validate(
        self,
        preparation: VectorIndexPreparationSpec,
    ) -> None:
        """Apply immutable Worker egress policy before secret resolution."""

        self._egress_policy.authorize(preparation.embedding)

    def create(
        self,
        preparation: VectorIndexPreparationSpec,
    ) -> EmbeddingProvider:
        self.validate(preparation)
        config = dict(preparation.embedding)
        config.pop("policy_profile", None)
        if config.get("provider") == "openai_compatible":
            config["follow_redirects"] = False
        secret_ref = str(config.pop("api_key_ref", "") or "").strip()
        if secret_ref:
            config["api_key"] = self._secret_resolver.resolve(SecretReference.parse(secret_ref))
        return build_embedding_provider(config)


class VectorIndexPreparationService:
    """Closed worker registry for document-to-point strategies."""

    def __init__(
        self,
        *,
        provider_factory: TaskEmbeddingProviderFactory | None = None,
        preparers: Sequence[VectorDocumentPreparer] | None = None,
    ) -> None:
        self._provider_factory = provider_factory or TaskEmbeddingProviderFactory()
        implementations = tuple(
            preparers
            or (
                CodeCompassDocumentPreparer(),
                WikiDocumentPreparer(),
            )
        )
        self._preparers = {implementation.kind: implementation for implementation in implementations}
        if len(self._preparers) != len(implementations):
            raise ValueError("vector_index_preparation_kind_duplicate")

    def validate_embedding_egress(
        self,
        preparation: Mapping[str, Any],
    ) -> None:
        """Fail before artifact, store, secret or provider access."""

        self._provider_factory.validate(VectorIndexPreparationSpec.from_mapping(preparation))

    def prepare(
        self,
        *,
        document_input: Mapping[str, Any],
        scope: VectorScope,
        compatibility: CompatibilitySpec,
        preparation: Mapping[str, Any],
    ) -> tuple[PreparedVectorPoint, ...]:
        spec = VectorIndexPreparationSpec.from_mapping(preparation)
        spec.validate_scope_domain(scope.domain)
        if document_input.get("schema") != VECTOR_INDEX_DOCUMENT_INPUT_SCHEMA:
            raise ValueError("vector_index_document_input_schema_invalid")
        if document_input.get("kind") != spec.kind:
            raise ValueError("vector_index_document_input_kind_mismatch")
        documents = document_input.get("documents")
        if not isinstance(documents, Sequence) or isinstance(
            documents,
            (str, bytes, bytearray),
        ):
            raise ValueError("vector_index_documents_invalid")
        if not documents:
            raise ValueError("vector_index_documents_required")
        if any(not isinstance(item, Mapping) for item in documents):
            raise ValueError("vector_index_documents_invalid")
        preparer = self._preparers.get(spec.kind)
        if preparer is None:
            raise ValueError("vector_index_preparation_kind_invalid")
        provider = self._provider_factory.create(spec)
        spec.validate_compatibility(compatibility)
        self._validate_compatibility(
            provider=provider,
            compatibility=compatibility,
            preparation=spec,
        )
        return preparer.prepare(
            documents=tuple(dict(item) for item in documents),
            scope=scope,
            provider=provider,
            compatibility=compatibility,
            preparation=spec,
        )

    @staticmethod
    def _validate_compatibility(
        *,
        provider: EmbeddingProvider,
        compatibility: CompatibilitySpec,
        preparation: VectorIndexPreparationSpec,
    ) -> None:
        if int(provider.dimensions) != compatibility.dimensions:
            raise ValueError("dimensions_mismatch")
        if str(provider.provider_id) != compatibility.provider:
            raise ValueError("provider_changed")
        if str(provider.model_version) != compatibility.model:
            raise ValueError("model_changed")
        if preparation.embedding_text_profile != compatibility.profile:
            raise ValueError("profile_changed")


def _validate_vectors(
    vectors: Sequence[Sequence[float]],
    documents: Sequence[Any],
    compatibility: CompatibilitySpec,
) -> None:
    if len(vectors) != len(documents):
        raise ValueError("embedding_response_size_mismatch")
    if any(len(tuple(vector)) != compatibility.dimensions for vector in vectors):
        raise ValueError("dimensions_mismatch")


def _bounded_required(
    value: Any,
    field: str,
    maximum_characters: int,
) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum_characters or any(ord(character) < 32 for character in text):
        raise ValueError(f"vector_index_{field}_invalid")
    return text


def _bounded_optional(
    value: Any,
    default: str,
    maximum_characters: int,
) -> str:
    if value is None:
        return default
    text = str(value).strip()
    if len(text) > maximum_characters or any(ord(character) < 32 for character in text):
        raise ValueError("vector_index_document_field_invalid")
    return text or default


def _bounded_score(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("vector_index_importance_score_invalid")
    result = float(value)
    if not 0.0 <= result <= 1_000_000.0:
        raise ValueError("vector_index_importance_score_invalid")
    return result


__all__ = [
    "CODECOMPASS_DOCUMENTS",
    "TaskEmbeddingProviderFactory",
    "VECTOR_INDEX_DOCUMENT_INPUT_SCHEMA",
    "VECTOR_INDEX_PREPARATION_SCHEMA",
    "VectorDocumentPreparer",
    "VectorIndexPreparationService",
    "VectorIndexPreparationSpec",
    "WIKI_DOCUMENTS",
]
