"""Voice deadline budgets and live-run preview context for requests and streams."""

from __future__ import annotations

from copy import deepcopy
from typing import (
    Any,
    Mapping,
)

from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_live_run_preview_service import (
    VoiceLiveRunPreviewBinding,
    VoiceLiveRunPreviewService,
)

_VOICE_STREAM_FINALIZATION_GRACE_SECONDS = 5.0


def _deadline_epoch_ms(*, request_started_epoch_ms: int, budget_seconds: float) -> int:
    return request_started_epoch_ms + max(1, round(float(budget_seconds) * 1000))


def _stream_deadline_budget(
    *,
    requested_deadline_seconds: float,
    max_audio_seconds: float,
    candidate_deadline_seconds: float,
) -> float:
    """Bound total stream lifetime without treating candidate time as capture time."""

    required_seconds = (
        max(0.001, float(max_audio_seconds))
        + max(0.1, float(candidate_deadline_seconds))
        + _VOICE_STREAM_FINALIZATION_GRACE_SECONDS
    )
    return max(1.0, min(float(requested_deadline_seconds), required_seconds, 300.0))


def _stream_request_context(
    body: Mapping[str, Any],
    binding: VoiceLiveRunPreviewBinding | None,
) -> tuple[str, str | None, str | None]:
    """Resolve optional request values against an authoritative preview binding."""

    if binding is None:
        return (
            str(body.get("profile_id") or "default"),
            str(body.get("configuration_session_id") or "").strip() or None,
            str(body.get("language") or "").strip() or None,
        )
    profile_id = body.get("profile_id") if "profile_id" in body else binding.profile_id
    configuration_session_id = (
        body.get("configuration_session_id")
        if "configuration_session_id" in body
        else binding.configuration_session_id
    )
    language = body.get("language") if "language" in body else binding.language
    return (
        str(profile_id or "default"),
        str(configuration_session_id or "").strip() or None,
        str(language or "").strip() or None,
    )


def _stream_preview_payload(
    binding: VoiceLiveRunPreviewBinding | None,
) -> dict[str, Any]:
    if binding is None:
        return {}
    return {
        "live_run_id": binding.live_run_id,
        "live_run_segment_sequence": binding.live_run_segment_sequence,
    }


def _assert_stream_preview_context(
    service: VoiceLiveRunPreviewService,
    principal: VoicePrincipal,
    binding: VoiceLiveRunPreviewBinding | None,
    *,
    profile_id: str,
    configuration_session_id: str | None,
    language: str | None,
) -> None:
    if binding is None:
        return
    service.assert_context(
        binding,
        profile_id=profile_id,
        configuration_session_id=configuration_session_id,
        language=language,
    )
    service.assert_current(principal, binding)


def _context_with_remaining_deadline(context: dict | None, remaining_seconds: float) -> dict | None:
    if not isinstance(context, dict):
        return context
    projected = deepcopy(context)
    # Hub-only policy is retained during orchestration but never crosses the
    # runtime boundary. The Runtime receives only its strict execution subset.
    projected.pop("_hub_configuration", None)
    configuration = projected.get("configuration")
    if isinstance(configuration, dict):
        configured = float(configuration.get("candidate_deadline_sec") or remaining_seconds)
        configuration["candidate_deadline_sec"] = max(0.001, min(configured, remaining_seconds))
    return projected
