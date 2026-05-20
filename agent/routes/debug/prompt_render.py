"""Debug API: POST /debug/prompts/render-dry-run. PTI-018."""
from __future__ import annotations

import logging

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import api_response

logger = logging.getLogger(__name__)

prompt_render_bp = Blueprint("debug_prompt_render", __name__)


@prompt_render_bp.route("/debug/prompts/render-dry-run", methods=["POST"])
@check_auth
def render_dry_run():
    """Render a planning prompt without calling any provider."""
    from agent.services.planning_prompt_registry import get_planning_prompt_registry
    from agent.services.prompt_trace_service import get_prompt_trace_service, prompt_hash
    from agent.services.prompt_provenance import PromptProvenanceChain, text_hash

    body = request.get_json(silent=True) or {}
    goal = str(body.get("goal") or "").strip()
    context = str(body.get("context") or "").strip() or None
    mode = str(body.get("mode") or "generic").strip()
    language = str(body.get("language") or "de").strip()
    model = body.get("model")
    model_family = str(body.get("model_family") or "").strip() or None
    preferred_prompt_version_id = str(body.get("preferred_prompt_version_id") or "").strip() or None
    preferred_output_format = str(body.get("preferred_output_format") or "").strip() or None
    domain_hints = list(body.get("domain_hints") or [])
    persist_trace = bool(body.get("persist_trace", False))
    provider = str(body.get("provider") or "").strip() or None

    if not goal:
        return api_response(data={"error": "goal is required"}, status=400)

    try:
        registry = get_planning_prompt_registry()
        resolved = registry.resolve(
            goal=goal,
            context=context,
            mode=mode,
            language=language,
            model_family=model_family,
            preferred_prompt_version_id=preferred_prompt_version_id,
            preferred_output_format=preferred_output_format,
            domain_hints=domain_hints,
        )
    except Exception as exc:
        logger.error("render_dry_run failed: %s", exc)
        return api_response(data={"error": "render_failed", "detail": str(exc)}, status=500)

    # Build provenance chain
    chain = PromptProvenanceChain()
    is_fallback = resolved.prompt_version_id == "inline-fallback"
    chain.add_planning_prompt(
        prompt_version_id=resolved.prompt_version_id,
        version=resolved.version,
        language=resolved.language,
        mode=resolved.mode,
        checksum=resolved.checksum,
        is_inline_fallback=is_fallback,
    )
    p_hash = prompt_hash(resolved.prompt)
    chain.add_final_render(output_hash=p_hash)

    # Redact for display
    from agent.services.prompt_redaction_service import get_redaction_service
    redaction_result = get_redaction_service().redact(resolved.prompt)
    prompt_redacted = redaction_result.redacted_text

    result = {
        "rendered": True,
        "provider_called": False,
        "goal": goal,
        "mode": mode,
        "language": language,
        "prompt_version_id": resolved.prompt_version_id,
        "version": resolved.version,
        "checksum": resolved.checksum,
        "prompt_hash_sha256": p_hash,
        "final_prompt_redacted": prompt_redacted,
        "template_chain": chain.to_list(),
        "secrets_detected": redaction_result.secrets_detected,
    }

    if persist_trace:
        try:
            svc = get_prompt_trace_service()
            trace = svc.create_trace(
                provider=provider,
                model=model,
                source_component="render_dry_run",
                request_kind="dry_run",
                prompt=resolved.prompt,
                template_chain=chain.to_list(),
            )
            finalized = svc.finalize_trace(trace, success=None)
            svc.store(finalized)
            result["trace_id"] = finalized.trace_id
        except Exception as exc:
            logger.warning("Could not persist dry_run trace: %s", exc)

    return api_response(data=result, status=200)
