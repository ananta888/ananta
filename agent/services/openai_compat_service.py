from __future__ import annotations

import time
from typing import Any

from flask import current_app

from agent.llm_integration import extract_llm_text_and_usage, generate_text
from agent.local_llm_backends import get_local_openai_backends, list_openai_compatible_models
from agent.repository import artifact_repo, artifact_version_repo, extracted_document_repo


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text", "input_text", "output_text"}:
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


class OpenAICompatService:
    """Thin OpenAI-style adapter over the existing hub services."""

    def list_models(self) -> list[dict[str, Any]]:
        agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
        provider_urls = current_app.config.get("PROVIDER_URLS", {}) or {}
        default_provider = str(agent_cfg.get("default_provider") or "")
        default_model = str(agent_cfg.get("default_model") or "")
        items: list[dict[str, Any]] = []
        now = int(time.time())

        static_models = {
            "openai": ["gpt-4o", "gpt-4-turbo"],
            "codex": ["gpt-5-codex", "gpt-5-codex-mini"],
            "anthropic": ["claude-3-5-sonnet-20240620"],
            "ollama": ["llama3"],
        }
        for provider, models in static_models.items():
            for model in models:
                model_id = f"{provider}:{model}"
                items.append(
                    {
                        "id": model_id,
                        "object": "model",
                        "created": now,
                        "owned_by": "ananta",
                        "provider": provider,
                        "selected": default_provider == provider and default_model == model,
                    }
                )

        for backend in get_local_openai_backends(
            agent_cfg=agent_cfg,
            provider_urls=provider_urls,
            default_provider=default_provider,
            default_model=default_model,
        ):
            dynamic_models = list_openai_compatible_models(backend.get("base_url"), timeout=5)
            for item in dynamic_models:
                model = str(item.get("id") or "").strip()
                if not model:
                    continue
                items.append(
                    {
                        "id": f"{backend['provider']}:{model}",
                        "object": "model",
                        "created": now,
                        "owned_by": "ananta",
                        "provider": backend["provider"],
                        "selected": default_provider == backend["provider"] and default_model == model,
                    }
                )
        deduped: dict[str, dict[str, Any]] = {}
        for item in items:
            deduped[item["id"]] = item
        return list(deduped.values())

    def _resolve_model(self, raw_model: str | None) -> tuple[str | None, str | None]:
        value = str(raw_model or "").strip()
        if not value:
            return None, None
        if ":" in value:
            provider, model = value.split(":", 1)
            return provider.strip() or None, model.strip() or None
        return None, value

    def chat_completion(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        messages = payload.get("messages") or []
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages_required")

        history: list[dict[str, str]] = []
        prompt = ""
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user").strip() or "user"
            content = _message_text(message.get("content"))
            if not content:
                continue
            history.append({"role": role, "content": content})
        if not history:
            raise ValueError("messages_required")
        prompt = history[-1]["content"]
        prior_history = history[:-1]

        provider_override, model_name = self._resolve_model(payload.get("model"))
        result = generate_text(
            prompt=prompt,
            provider=provider_override,
            model=model_name,
            history=prior_history,
            temperature=payload.get("temperature"),
        )
        text, usage = extract_llm_text_and_usage(result)
        created = int(time.time())
        response_model = payload.get("model") or f"{provider_override}:{model_name}" if provider_override and model_name else (model_name or "")
        return {
            "id": f"chatcmpl-{created}",
            "object": "chat.completion",
            "created": created,
            "model": response_model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": usage,
            "trace_id": (result.get("trace_id") if isinstance(result, dict) else None),
        }

    def response_api(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        raw_input = payload.get("input")
        prompt = _message_text(raw_input)
        if not prompt and isinstance(raw_input, list):
            prompt = "\n".join(_message_text(item.get("content")) for item in raw_input if isinstance(item, dict)).strip()
        if not prompt:
            raise ValueError("input_required")
        provider_override, model_name = self._resolve_model(payload.get("model"))
        result = generate_text(
            prompt=prompt,
            provider=provider_override,
            model=model_name,
            temperature=payload.get("temperature"),
        )
        text, usage = extract_llm_text_and_usage(result)
        created = int(time.time())
        response_model = payload.get("model") or f"{provider_override}:{model_name}" if provider_override and model_name else (model_name or "")
        return {
            "id": f"resp-{created}",
            "object": "response",
            "created_at": created,
            "model": response_model,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
            "output_text": text,
            "usage": usage,
            "trace_id": (result.get("trace_id") if isinstance(result, dict) else None),
        }

    def list_files(self) -> list[dict[str, Any]]:
        return [self._serialize_file(item.id) for item in artifact_repo.get_all()]

    def _serialize_file(self, artifact_id: str) -> dict[str, Any]:
        artifact = artifact_repo.get_by_id(artifact_id)
        if artifact is None:
            raise KeyError(artifact_id)
        version = artifact_version_repo.get_by_id(artifact.latest_version_id) if artifact.latest_version_id else None
        documents = extracted_document_repo.get_by_artifact(artifact_id)
        return {
            "id": artifact.id,
            "object": "file",
            "bytes": artifact.size_bytes,
            "created_at": int(artifact.created_at),
            "filename": artifact.latest_filename,
            "purpose": "assistants",
            "status": artifact.status,
            "media_type": artifact.latest_media_type,
            "sha256": artifact.latest_sha256,
            "version_id": version.id if version else None,
            "extracted_document_count": len(documents),
        }


openai_compat_service = OpenAICompatService()


def get_openai_compat_service() -> OpenAICompatService:
    return openai_compat_service
