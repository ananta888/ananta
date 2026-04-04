from __future__ import annotations

import time
import uuid
from typing import Any

from flask import current_app

from agent.local_llm_backends import list_openai_compatible_models
from agent.repository import artifact_repo, artifact_version_repo, extracted_document_repo
from agent.services.hub_llm_service import generate_text_and_usage
from agent.services.integration_registry_service import get_integration_registry_service


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
        return get_integration_registry_service().list_openai_compat_models(
            agent_cfg=agent_cfg,
            provider_urls=provider_urls,
            default_provider=default_provider,
            default_model=default_model,
            model_lister=list_openai_compatible_models,
        )

    def _resolve_model(self, raw_model: str | None) -> tuple[str | None, str | None]:
        value = str(raw_model or "").strip()
        if not value:
            return None, None
        if ":" in value:
            provider, model = value.split(":", 1)
            return provider.strip() or None, model.strip() or None
        return None, value

    def _session_metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        session_id = str(payload.get("session_id") or metadata.get("session_id") or "").strip() or None
        conversation_id = str(payload.get("conversation_id") or metadata.get("conversation_id") or "").strip() or None
        if not session_id and not conversation_id:
            return {}
        return {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "turn_id": f"turn-{uuid.uuid4()}",
            "mode": "metadata_echo_v1",
        }

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
        text, usage, result = generate_text_and_usage(
            prompt=prompt,
            provider=provider_override,
            model=model_name,
            history=prior_history,
            temperature=payload.get("temperature"),
        )
        created = int(time.time())
        response_model = payload.get("model") or f"{provider_override}:{model_name}" if provider_override and model_name else (model_name or "")
        response = {
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
        session_meta = self._session_metadata(payload)
        if session_meta:
            response["conversation"] = session_meta
        return response

    def response_api(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        raw_input = payload.get("input")
        prompt = _message_text(raw_input)
        if not prompt and isinstance(raw_input, list):
            prompt = "\n".join(_message_text(item.get("content")) for item in raw_input if isinstance(item, dict)).strip()
        if not prompt:
            raise ValueError("input_required")
        provider_override, model_name = self._resolve_model(payload.get("model"))
        text, usage, result = generate_text_and_usage(
            prompt=prompt,
            provider=provider_override,
            model=model_name,
            temperature=payload.get("temperature"),
        )
        created = int(time.time())
        response_model = payload.get("model") or f"{provider_override}:{model_name}" if provider_override and model_name else (model_name or "")
        response = {
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
        session_meta = self._session_metadata(payload)
        if session_meta:
            response["conversation"] = session_meta
        return response

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
