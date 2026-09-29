"""Collaborators of the Hub Voice routes (``/v1/voice/*``) and their single override seam.

The Voice handlers delegate provider calls, admission, result artifacts,
correction/judge/restricted-choice services, exposure policy, share-session and
consent lookups, audit logging, recognition context, admission limits and the
artifact-first Hub execution to these collaborators. Handlers obtain them via
:func:`voice_route_dependencies`; tests replace them per application with
``VOICE_ROUTE_DEPENDENCIES.override(app, ...)``.

:func:`agent.routes.voice_hub_execution._execute_hub_voice_request` itself
receives the bundle as an explicit argument (it is part of the bundle, so it
cannot resolve it without an import cycle); :meth:`VoiceRouteDependencies.run_hub_voice_request`
passes it along.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent.common.audit import log_audit
from agent.routes.route_dependency_seam import RouteDependencySeam
from agent.routes.voice_hub_execution import _execute_hub_voice_request
from agent.routes.voice_recognition_context import _recognition_context
from agent.routes.voice_request_support import (
    _store_audio_enabled,
    _voice_admission_limits,
)
from agent.services.exposure_policy_service import get_exposure_policy_service
from agent.services.share_session_service import get_share_session_service
from agent.services.speech_evidence_consent_service import get_speech_evidence_consent_service
from agent.services.voice_admission_service import get_voice_admission_service
from agent.services.voice_generative_corrector_service import (
    generative_corrector_capability_bundle,
    get_voice_generative_corrector_service,
)
from agent.services.voice_generative_judge_service import get_voice_generative_judge_service
from agent.services.voice_provider import get_voice_provider_service
from agent.services.voice_restricted_choice_service import get_voice_restricted_choice_service
from agent.services.voice_result_artifact_service import get_voice_result_artifact_service


@dataclass(frozen=True)
class VoiceRouteDependencies:
    """Services, policies and the Hub executor the Voice routes delegate to."""

    get_voice_provider_service: Callable[[], Any]
    get_voice_admission_service: Callable[[], Any]
    get_voice_result_artifact_service: Callable[[], Any]
    get_voice_generative_corrector_service: Callable[[], Any]
    get_voice_generative_judge_service: Callable[[], Any]
    get_voice_restricted_choice_service: Callable[[], Any]
    get_exposure_policy_service: Callable[[], Any]
    get_share_session_service: Callable[[], Any]
    get_speech_evidence_consent_service: Callable[[], Any]
    generative_corrector_capability_bundle: Callable[..., Any]
    log_audit: Callable[..., Any]
    recognition_context: Callable[..., Any]
    admission_limits: Callable[[], Any]
    store_audio_enabled: Callable[[], bool]
    execute_hub_voice_request: Callable[..., Any]

    def run_hub_voice_request(self, **kwargs: Any) -> Any:
        """Run the artifact-first Hub execution with this bundle as its collaborators."""

        return self.execute_hub_voice_request(dependencies=self, **kwargs)


def production_voice_route_dependencies() -> VoiceRouteDependencies:
    return VoiceRouteDependencies(
        get_voice_provider_service=get_voice_provider_service,
        get_voice_admission_service=get_voice_admission_service,
        get_voice_result_artifact_service=get_voice_result_artifact_service,
        get_voice_generative_corrector_service=get_voice_generative_corrector_service,
        get_voice_generative_judge_service=get_voice_generative_judge_service,
        get_voice_restricted_choice_service=get_voice_restricted_choice_service,
        get_exposure_policy_service=get_exposure_policy_service,
        get_share_session_service=get_share_session_service,
        get_speech_evidence_consent_service=get_speech_evidence_consent_service,
        generative_corrector_capability_bundle=generative_corrector_capability_bundle,
        log_audit=log_audit,
        recognition_context=_recognition_context,
        admission_limits=_voice_admission_limits,
        store_audio_enabled=_store_audio_enabled,
        execute_hub_voice_request=_execute_hub_voice_request,
    )


VOICE_ROUTE_DEPENDENCIES: RouteDependencySeam[VoiceRouteDependencies] = RouteDependencySeam(
    "ananta.voice_route_dependencies",
    production_voice_route_dependencies,
)


def voice_route_dependencies() -> VoiceRouteDependencies:
    """The Voice collaborators of the current application."""

    return VOICE_ROUTE_DEPENDENCIES.resolve()
