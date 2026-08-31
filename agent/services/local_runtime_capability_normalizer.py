"""Provider-neutral normalization of bounded Ollama and LM Studio metadata."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.local_runtime_capability_contracts import (
    CAPABILITY_NAMES,
    RuntimeCapabilityClaim,
    RuntimeModelSnapshot,
    utc_now,
)
from agent.services.local_runtime_template_inspector import LocalRuntimeTemplateInspector


class LocalRuntimeCapabilityNormalizer:
    def __init__(self, inspector: LocalRuntimeTemplateInspector | None = None) -> None:
        self._inspector = inspector or LocalRuntimeTemplateInspector()

    def normalize(
        self,
        *,
        provider_id: str,
        model_id: str,
        runtime_version: str,
        metadata: Mapping[str, Any],
        model_digest: str | None = None,
        discovered_at: str | None = None,
    ) -> RuntimeModelSnapshot:
        provider = str(provider_id).strip().lower()
        when = discovered_at or utc_now()
        digest = self._digest(model_digest, metadata, model_id)
        inspection = self._inspector.inspect(metadata.get("template"))
        raw_capabilities = metadata.get("capabilities")
        named = self._capability_names(raw_capabilities)
        model_kind = self._model_kind(metadata, named)
        claims: list[RuntimeCapabilityClaim] = []
        for name in sorted(CAPABILITY_NAMES):
            explicit = self._explicit_capability(metadata, named, name, model_kind)
            if explicit is None:
                continue
            claims.append(RuntimeCapabilityClaim(name, explicit, "runtime_reported", 1.0, when))
        conflicts: list[str] = []
        if inspection.conflict:
            conflicts.append("template_conflict")
        if model_kind == "embedding" and any(item.name == "chat" and item.supported for item in claims):
            conflicts.append("embedding_chat_conflict")
            claims = [item for item in claims if item.name != "chat"] + [
                RuntimeCapabilityClaim("chat", False, "runtime_reported", 1.0, when)
            ]
        return RuntimeModelSnapshot(
            provider_id=provider,
            model_id=str(model_id).strip(),
            model_digest=digest,
            runtime_version=str(runtime_version or "unknown").strip(),
            model_kind=model_kind,
            context_window=self._context_window(metadata),
            template_family=inspection.family,
            template_sha256=inspection.sha256,
            capabilities=tuple(sorted(claims, key=lambda item: item.name)),
            conflicts=tuple(sorted(set(conflicts))),
            discovered_at=when,
        )

    @staticmethod
    def _digest(value: str | None, metadata: Mapping[str, Any], model_id: str) -> str:
        candidate = str(value or metadata.get("digest") or metadata.get("sha256") or "").lower()
        if len(candidate) == 64 and all(character in "0123456789abcdef" for character in candidate):
            return candidate
        # A synthetic content digest is an internal cache binding only. It is
        # deliberately not represented as SRC_* or release evidence.
        stable = f"{model_id}\0{metadata.get('modified_at', '')}\0{metadata.get('size', '')}"
        return hashlib.sha256(stable.encode()).hexdigest()

    @staticmethod
    def _capability_names(value: object) -> set[str]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            return set()
        aliases = {"tool_calling": "tools", "embed": "embedding"}
        return {aliases.get(str(item).strip().lower(), str(item).strip().lower()) for item in value}

    @staticmethod
    def _model_kind(metadata: Mapping[str, Any], named: set[str]) -> str:
        kind = str(metadata.get("type") or metadata.get("model_type") or metadata.get("kind") or "").lower()
        if kind in {"embedding", "embeddings"} or "embedding" in named:
            return "embedding"
        if kind in {"llm", "chat", "language_model"} or named.intersection(
            {"chat", "completion", "tools", "vision", "thinking"}
        ):
            return "chat"
        return "unknown"

    @staticmethod
    def _explicit_capability(metadata: Mapping[str, Any], named: set[str], name: str, model_kind: str) -> bool | None:
        keys = (name, f"supports_{name}")
        for key in keys:
            if type(metadata.get(key)) is bool:
                return bool(metadata[key])
        if name in named:
            return True
        if name == "chat" and "completion" in named:
            return True
        if name == "chat" and model_kind == "embedding":
            return False
        return None

    @staticmethod
    def _context_window(metadata: Mapping[str, Any]) -> int | None:
        for key in ("context_window", "context_length", "max_context_length", "num_ctx"):
            value = metadata.get(key)
            if type(value) is int and 1 <= value <= 100_000_000:
                return value
        details = metadata.get("details")
        if isinstance(details, Mapping):
            return LocalRuntimeCapabilityNormalizer._context_window(details)
        return None


__all__ = ["LocalRuntimeCapabilityNormalizer"]
