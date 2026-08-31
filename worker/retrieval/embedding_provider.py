from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol
from urllib import error, request
from urllib.parse import urlparse, urlunparse

from agent.services.local_runtime_response_adapters import (
    LocalRuntimeResponseError,
    normalize_ollama_embedding,
)


class EmbeddingProvider(Protocol):
    provider_id: str
    model_version: str
    dimensions: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class EmbeddingProviderError(RuntimeError):
    """Raised when embedding provider execution fails."""


class EmbeddingProviderUnavailable(EmbeddingProviderError):
    """Raised when a provider is configured but unavailable."""


class EmbeddingProviderRequestFailed(EmbeddingProviderError):
    """Raised when a remote provider request fails."""


class _EmbeddingNoRedirectHandler(request.HTTPRedirectHandler):
    """Prevent credentials from following an embedding HTTP redirect."""

    def redirect_request(self, *_args: Any, **_kwargs: Any):
        raise EmbeddingProviderRequestFailed(
            "embedding_provider_redirect_forbidden"
        )


def _hash_vector(value: str, *, dimensions: int) -> list[float]:
    dims = max(1, int(dimensions))
    digest = sha256(str(value or "").encode("utf-8")).digest()
    bucket = [0.0 for _ in range(dims)]
    for index, byte in enumerate(digest):
        bucket[index % dims] += float(byte) / 255.0
    normalized = max(sum(abs(item) for item in bucket), 1e-9)
    return [item / normalized for item in bucket]


def _default_url_port(scheme: str) -> int | None:
    if scheme.lower() == "http":
        return 80
    if scheme.lower() == "https":
        return 443
    return None


def _base_url_allowed(base_url: str, allowed_base_urls: list[str]) -> bool:
    candidate = urlparse(str(base_url or "").rstrip("/"))
    if not candidate.scheme or not candidate.netloc:
        return False
    candidate_path = (candidate.path or "").rstrip("/")
    for raw_allowed in allowed_base_urls:
        allowed = urlparse(str(raw_allowed or "").rstrip("/"))
        if not allowed.scheme or not allowed.netloc:
            continue
        if candidate.scheme.lower() != allowed.scheme.lower():
            continue
        if candidate.hostname != allowed.hostname:
            continue
        if (candidate.port or _default_url_port(candidate.scheme)) != (
            allowed.port or _default_url_port(allowed.scheme)
        ):
            continue
        allowed_path = (allowed.path or "").rstrip("/")
        if not allowed_path:
            return True
        if candidate_path == allowed_path or candidate_path.startswith(f"{allowed_path}/"):
            return True
    return False


@dataclass(frozen=True)
class HashEmbeddingProvider:
    provider_id: str = "local_hash"
    model_version: str = "hash-v1"
    dimensions: int = 12

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_hash_vector(str(value or ""), dimensions=self.dimensions) for value in list(texts or [])]


@dataclass(frozen=True)
class FakeEmbeddingProvider:
    provider_id: str = "fake_test"
    model_version: str = "fake-v1"
    dimensions: int = 8

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_hash_vector(f"fake::{str(value or '')}", dimensions=self.dimensions) for value in list(texts or [])]


@dataclass(frozen=True)
class OpenAICompatibleEmbeddingProvider:
    base_url: str
    api_key: str | None = None
    model: str = "text-embedding-3-small"
    provider_id: str = "openai_compatible"
    model_version: str = "text-embedding-3-small"
    dimensions: int = 1536
    timeout_seconds: int = 20
    follow_redirects: bool = True

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not str(self.base_url or "").strip():
            raise EmbeddingProviderUnavailable("missing_embedding_base_url")
        if not str(self.api_key or "").strip():
            raise EmbeddingProviderUnavailable("missing_embedding_api_key")
        payload = {"input": [str(item or "") for item in list(texts or [])], "model": self.model}
        endpoint = str(self.base_url).rstrip("/") + "/embeddings"
        req = request.Request(
            endpoint,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            opener = (
                request.urlopen
                if self.follow_redirects
                else request.build_opener(
                    _EmbeddingNoRedirectHandler()
                ).open
            )
            with opener(req, timeout=max(1, int(self.timeout_seconds))) as response:
                raw = response.read().decode("utf-8")
        except error.URLError as exc:
            raise EmbeddingProviderRequestFailed(f"embedding_provider_request_failed:{exc}") from exc
        parsed = json.loads(raw)
        rows = list(parsed.get("data") or [])
        vectors: list[list[float]] = []
        for row in rows:
            embedding = [float(item) for item in list((row or {}).get("embedding") or [])]
            vectors.append(embedding)
        if len(vectors) != len(payload["input"]):
            raise EmbeddingProviderRequestFailed("embedding_provider_response_size_mismatch")
        return vectors


@dataclass(frozen=True)
class OllamaEmbeddingProvider:
    base_url: str
    model: str
    model_version: str
    dimensions: int
    allowed_base_urls: tuple[str, ...]
    provider_id: str = "ollama"
    timeout_seconds: int = 20
    maximum_response_bytes: int = 2 * 1024 * 1024

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        inputs = [str(item or "") for item in list(texts or [])]
        if not inputs:
            return []
        if not self.model or not _base_url_allowed(self.base_url, list(self.allowed_base_urls)):
            raise EmbeddingProviderUnavailable("ollama_embedding_endpoint_not_allowed")
        parsed = urlparse(str(self.base_url).rstrip("/"))
        endpoint = urlunparse((parsed.scheme, parsed.netloc, "/api/embed", "", "", ""))
        payload = {
            "model": self.model,
            "input": inputs,
            "truncate": False,
        }
        req = request.Request(
            endpoint,
            method="POST",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            opener = request.build_opener(_EmbeddingNoRedirectHandler()).open
            with opener(req, timeout=max(1, int(self.timeout_seconds))) as response:
                raw = response.read(self.maximum_response_bytes + 1)
        except error.URLError as exc:
            raise EmbeddingProviderRequestFailed("ollama_embedding_request_failed") from exc
        if len(raw) > self.maximum_response_bytes:
            raise EmbeddingProviderRequestFailed("ollama_embedding_response_too_large")
        try:
            parsed_response = json.loads(raw)
            rows = parsed_response.get("embeddings")
            if not isinstance(rows, list) or len(rows) != len(inputs):
                raise LocalRuntimeResponseError("embedding_response_size_mismatch")
            return [
                list(
                    normalize_ollama_embedding(
                        {"embeddings": [row]},
                        expected_dimension=self.dimensions,
                    )
                )
                for row in rows
            ]
        except (TypeError, ValueError, json.JSONDecodeError, LocalRuntimeResponseError) as exc:
            reason = str(exc)
            if reason not in {"embedding_dimension_mismatch", "embedding_response_size_mismatch"}:
                reason = "ollama_embedding_response_invalid"
            raise EmbeddingProviderRequestFailed(reason) from exc


def build_embedding_provider(config: dict[str, Any] | None = None) -> EmbeddingProvider:
    payload = dict(config or {})
    provider = str(payload.get("provider") or "fake").strip().lower() or "fake"
    dimensions = max(1, int(payload.get("dimensions") or 8))

    # EPC-006: external providers require explicit opt-in
    is_external = provider in {"openai", "openai_compatible"}
    if is_external and not payload.get("external_calls_allowed", False):
        raise ValueError(
            "embedding_provider_external_calls_not_allowed: "
            "set external_calls_allowed=True to use "
            "OpenAI-compatible providers"
        )
    if is_external and payload.get("allowed_base_urls"):
        base_url = str(payload.get("base_url") or "").rstrip("/")
        allowed = [str(u).rstrip("/") for u in payload["allowed_base_urls"]]
        if base_url and not _base_url_allowed(base_url, allowed):
            raise ValueError(
                f"embedding_provider_base_url_not_allowed:{base_url!r}"
            )

    if provider in {"fake", "test"}:
        return FakeEmbeddingProvider(
            provider_id=str(payload.get("provider_id") or "fake_test"),
            model_version=str(payload.get("model_version") or "fake-v1"),
            dimensions=dimensions,
        )
    if provider in {"local", "local_hash", "hash"}:
        return HashEmbeddingProvider(
            provider_id=str(payload.get("provider_id") or "local_hash"),
            model_version=str(payload.get("model_version") or "hash-v1"),
            dimensions=dimensions,
        )
    if provider in {"openai", "openai_compatible"}:
        model = str(payload.get("model") or "text-embedding-3-small").strip() or "text-embedding-3-small"
        return OpenAICompatibleEmbeddingProvider(
            base_url=str(payload.get("base_url") or "").strip(),
            api_key=str(payload.get("api_key") or "").strip() or None,
            model=model,
            provider_id=str(payload.get("provider_id") or "openai_compatible"),
            model_version=str(payload.get("model_version") or model),
            dimensions=max(1, int(payload.get("dimensions") or 1536)),
            timeout_seconds=max(1, int(payload.get("timeout_seconds") or 20)),
            follow_redirects=bool(
                payload.get("follow_redirects", True)
            ),
        )
    if provider in {"ollama", "ollama_native"}:
        base_url = str(payload.get("base_url") or "").strip()
        allowed = tuple(str(item).strip() for item in payload.get("allowed_base_urls") or () if str(item).strip())
        if not base_url or not allowed or not _base_url_allowed(base_url, list(allowed)):
            raise ValueError("ollama_embedding_endpoint_not_allowed")
        model = str(payload.get("model") or "").strip()
        if not model:
            raise ValueError("ollama_embedding_model_required")
        return OllamaEmbeddingProvider(
            base_url=base_url,
            model=model,
            model_version=str(payload.get("model_version") or model),
            dimensions=dimensions,
            allowed_base_urls=allowed,
            timeout_seconds=max(1, int(payload.get("timeout_seconds") or 20)),
        )
    raise ValueError(f"unknown_embedding_provider:{provider}")
