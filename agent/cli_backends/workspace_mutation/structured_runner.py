"""Structured-output LLM runner for the workspace-mutation protocol.

Free-form sampling cannot be trusted to emit ``ananta_worker_mutation.v1``: one
unescaped quote inside ``files[].content`` closes the JSON string early and the
whole answer becomes unparseable (``invalid_output``, then
``invalid_output_limit_reached`` after ``max_invalid_outputs``). The local
OpenAI-compatible server instead builds the sampling grammar from
``response_format: json_schema``, so malformed JSON cannot be produced at all
and a run does not spend four extra turns on the same defect.

Selecting this runner is opt-in (``ananta_worker_workspace_mutation.
structured_output``); without that flag the CLI path stays the default, so
deployments that do not configure it keep their previous behaviour.

Signature compatible with the ``llm_runner`` seam of
``run_ananta_worker_workspace_mutation``: it returns ``(rc, out, err)``.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from agent.cli_backends.helpers import (
    _get_agent_config,
    _get_runtime_default_provider,
    _get_runtime_provider_urls,
    _normalize_openai_base_url,
    _resolve_openai_compatible_base_url,
)
from agent.cli_backends.semaphore import _acquire_backend_permit
from agent.config import settings

log = logging.getLogger(__name__)

MUTATION_SCHEMA_NAME = "ananta_worker_mutation"
STRUCTURED_OUTPUT_TEMPERATURE = 0.2
_BEARER_PLACEHOLDER = "sk-no-key-needed"

MUTATION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind"],
    "anyOf": [
        {
            "properties": {
                "kind": {"const": "workspace_write"},
                "reason": {"type": "string"},
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["path", "content"],
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["kind", "reason", "files"],
            "additionalProperties": False,
        },
        {
            "properties": {
                "kind": {"const": "code_analysis"},
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["path", "file_sha256", "line_start", "line_end"],
                        "properties": {
                            "path": {"type": "string"},
                            "file_sha256": {"type": "string"},
                            "line_start": {"type": "integer"},
                            "line_end": {"type": "integer"},
                        },
                    },
                },
                "findings": {"type": "string"},
                "change_reasoning": {"type": "string"},
                "test_reasoning": {"type": "string"},
            },
            "required": ["kind", "sources", "findings", "change_reasoning", "test_reasoning"],
            "additionalProperties": False,
        },
        {
            "properties": {
                "kind": {"const": "tool_request"},
                "tool_name": {"type": "string"},
                "arguments": {"type": "object"},
                "command": {"type": "string"},
                "path": {"type": "string"},
                "line_start": {"type": "integer"},
                "line_end": {"type": "integer"},
            },
            "required": ["kind", "tool_name"],
            "additionalProperties": False,
        },
        {
            "properties": {
                "kind": {"const": "final_answer"},
                "answer": {"type": "string"},
                "summary_of_changes": {"type": "string"},
                "partial": {"type": "boolean"},
            },
            "required": ["kind", "answer"],
            "additionalProperties": False,
        },
        {
            "properties": {
                "kind": {"const": "patch_request"},
                "target_path": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["kind", "target_path"],
            "additionalProperties": False,
        },
        {
            "properties": {
                "kind": {"const": "needs_approval"},
                "reason": {"type": "string"},
            },
            "required": ["kind", "reason"],
            "additionalProperties": False,
        },
        {
            "properties": {
                "kind": {"const": "cannot_continue_without_context"},
                "reason": {"type": "string"},
            },
            "required": ["kind", "reason"],
            "additionalProperties": False,
        },
    ],
}


def _resolve_base_url() -> str | None:
    """Base URL of the active OpenAI-compatible chat endpoint.

    Ordered like the CLI path but with the two cases that path misses: the
    configured local backends (``local_openai_backends``) and the active model
    block (``llm_config.base_url``). Both are what a self-hosted runner such as
    ``llamacpp`` resolves to; the shared fallback deliberately stays last so an
    unrelated ``openai`` alias never wins over an explicitly registered runner.
    """
    agent_cfg = _get_agent_config()
    provider = _get_runtime_default_provider()
    provider_urls = _get_runtime_provider_urls()

    from agent.local_llm_backends import resolve_local_openai_backend

    local = resolve_local_openai_backend(
        provider, agent_cfg=agent_cfg, provider_urls=provider_urls
    )
    local_url = _normalize_openai_base_url((local or {}).get("base_url"))
    if local_url:
        return local_url

    llm_config = agent_cfg.get("llm_config") if isinstance(agent_cfg.get("llm_config"), dict) else {}
    configured_url = _normalize_openai_base_url(llm_config.get("base_url"))
    if configured_url and str(llm_config.get("provider") or provider) == provider:
        return configured_url

    return _resolve_openai_compatible_base_url()


def _resolve_model(model: str | None) -> str | None:
    agent_cfg = _get_agent_config()
    candidates = [
        model,
        agent_cfg.get("default_model"),
        agent_cfg.get("model"),
        (agent_cfg.get("llm_config") or {}).get("model")
        if isinstance(agent_cfg.get("llm_config"), dict)
        else None,
        settings.default_model,
    ]
    for candidate in candidates:
        resolved = str(candidate or "").strip()
        if resolved and resolved.lower() != "auto":
            return resolved
    return None


def run_structured_mutation_llm(
    prompt: str,
    options: list | None = None,
    timeout: int = 60,
    model: str | None = None,
    workdir: str | None = None,
) -> tuple[int, str, str]:
    """One protocol answer constrained to ``MUTATION_JSON_SCHEMA``.

    Returns ``(rc, out, err)`` like the CLI runner: ``rc == 0`` carries exactly
    the JSON object the mutation loop parses, a nonzero ``rc`` with an empty
    ``out`` ends the run as ``llm_failed`` instead of burning invalid-output
    turns.
    """
    base_url = _resolve_base_url()
    if not base_url:
        return -1, "", "structured_output_base_url_unresolved"

    resolved_model = _resolve_model(model)
    if not resolved_model:
        return -1, "", "structured_output_model_unresolved"

    payload = {
        "model": resolved_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": STRUCTURED_OUTPUT_TEMPERATURE,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": MUTATION_SCHEMA_NAME,
                "strict": True,
                "schema": MUTATION_JSON_SCHEMA,
            },
        },
    }
    body = json.dumps(payload).encode("utf-8")

    with _acquire_backend_permit("structured_output", timeout=timeout) as ticket:
        if not ticket.acquired:
            return -1, "", "Backend 'structured_output' ist ausgelastet (semaphore_exhausted)"

        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_BEARER_PLACEHOLDER}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=max(1, int(timeout))) as response:
                document = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500] if hasattr(exc, "read") else ""
            log.warning("structured_output http_error=%s detail=%s", exc.code, detail)
            return -1, "", f"structured_output_http_error:{exc.code}:{detail}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("structured_output request_failed: %s", exc)
            return -1, "", f"structured_output_request_failed:{exc}"
        except (json.JSONDecodeError, ValueError) as exc:
            log.warning("structured_output malformed_document: %s", exc)
            return -1, "", f"structured_output_malformed_document:{exc}"

    try:
        content = document["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log.warning("structured_output unexpected_document: %s", exc)
        return -1, "", f"structured_output_unexpected_document:{exc}"

    if not isinstance(content, str) or not content.strip():
        return -1, "", "structured_output_empty_answer"
    return 0, content, ""
