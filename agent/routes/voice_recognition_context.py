"""Voice recognition context and the Hub-to-Runtime configuration projection."""

from __future__ import annotations

from copy import deepcopy
from typing import (
    Any,
    Mapping,
)

from flask import (
    current_app,
    request,
)

from agent.services.voice_configuration_service import get_voice_configuration_service
from agent.services.voice_generative_corrector_service import (
    generative_corrector_capabilities,
    resolve_auto_corrector_configuration,
    resolve_inherited_corrector_configuration,
)
from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_personalization_service import get_voice_personalization_service


def _recognition_context(
    principal: VoicePrincipal,
    *,
    profile_id: str | None = None,
    session_id: str | None = None,
) -> dict | None:
    profile_id = profile_id or (str(request.form.get("profile_id") or "").strip() or None)
    session_id = session_id or (str(request.form.get("session_id") or "").strip() or None)
    configuration = get_voice_configuration_service().resolve(
        principal,
        legacy_global=current_app.config.get("AGENT_CONFIG", {}) or {},
        profile_id=profile_id,
        session_id=session_id,
    )
    hub_configuration = resolve_inherited_corrector_configuration(
        configuration.effective,
        current_app.config.get("AGENT_CONFIG", {}) or {},
    )
    if str(hub_configuration.get("generative_corrector_model") or "").casefold() == "auto":
        hub_configuration = resolve_auto_corrector_configuration(
            hub_configuration,
            generative_corrector_capabilities(),
        )
    context: dict = {
        "schema_version": "ananta.voice-recognition-context.v1",
        "configuration": _runtime_voice_configuration(configuration.effective),
        "_hub_configuration": deepcopy(hub_configuration),
    }
    if profile_id and configuration.effective["feature_flags"].get("personalization"):
        snapshot = get_voice_personalization_service().snapshot(principal, profile_id)
        context["personalization"] = {
            "schema_version": snapshot["schema_version"],
            "version": snapshot["version"],
            "consent_id": snapshot["consent_id"],
            "consent_version": snapshot["consent_version"],
            "consent_granted": snapshot["consent_granted"],
            "revocation_epoch": snapshot["revocation_epoch"],
            "expires_at": snapshot["expires_at"],
            "vocabulary": list(snapshot["vocabulary"]),
            "substitutions": list(snapshot["substitutions"]),
            "preferences": list(snapshot["preferences"]),
            "weights": dict(snapshot["weights"]),
            "persistence_owner": "hub",
            "runtime_persistence_allowed": False,
        }
    return context


def _runtime_voice_configuration(effective: Mapping[str, Any]) -> dict[str, Any]:
    """Project Hub-owned correction policy onto the strict Voice Runtime port."""

    runtime_fields = {
        "transport_mode",
        "recognition_strategy",
        "routing_strategy",
        "correction_policy",
        "review_policy",
        "primary_backend",
        "secondary_backends",
        "max_parallel_backends",
        "candidate_deadline_sec",
        "confidence_threshold",
        "enhancement_variants",
        "diarization_backend",
        "feature_flags",
    }
    projected = {key: deepcopy(value) for key, value in effective.items() if key in runtime_fields}
    if projected.get("correction_policy") == "generative_rewrite":
        projected["correction_policy"] = "deterministic"
    flags = projected.get("feature_flags")
    if isinstance(flags, dict):
        flags.pop("generative_corrector", None)
    return projected


def _hub_effective_configuration(context: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        return {}
    value = context.get("_hub_configuration", context.get("configuration"))
    return dict(value) if isinstance(value, Mapping) else {}
