"""Hub Voice blueprint (``/v1/voice/*``) - public entry point.

The former 2333-line module is split into single-responsibility siblings:

* :mod:`agent.routes.voice_blueprint` - the ``voice`` blueprint plus its
  request-body limits and 413 error handler.
* :mod:`agent.routes.voice_request_support` - request parsing, principal/audit
  identity, error envelopes, observation and exposure-policy guard.
* :mod:`agent.routes.voice_request_deadlines` - deadline budgets and live-run
  preview context for requests and streams.
* :mod:`agent.routes.voice_recognition_context` - recognition context and the
  Hub/Runtime configuration projection.
* :mod:`agent.routes.voice_hub_execution` - artifact-first Hub voice execution,
  recovery and deferred completion.
* :mod:`agent.routes.voice_transcription_routes` - capabilities and transcribe.
* :mod:`agent.routes.voice_source_correction_routes` - semantic speech source
  correction and its session/consent authorization.
* :mod:`agent.routes.voice_command_routes` - voice command and goal endpoints.
* :mod:`agent.routes.voice_stream_create_routes` - stream creation.
* :mod:`agent.routes.voice_stream_lifecycle_routes` - stream chunk, finalize,
  status and delete endpoints.

* :mod:`agent.routes.voice_request_policy` - exposure-policy guard and privacy
  state.
* :mod:`agent.routes.voice_route_dependencies` - the collaborators the Voice
  handlers delegate to, and their per-application override seam.

Importing this module registers every route on ``voice_bp``. All names that
used to live here stay importable from this module. The handlers' collaborators
(service getters, ``log_audit``, recognition context, admission limits, audio
storage policy and the Hub executor) are bundled in
:class:`agent.routes.voice_route_dependencies.VoiceRouteDependencies` and resolved
per application through ``VOICE_ROUTE_DEPENDENCIES``; tests replace them with
``VOICE_ROUTE_DEPENDENCIES.override(app, ...)`` instead of patching names on
this module. The re-exported service getters below are kept for import
compatibility only.
"""

from __future__ import annotations

from agent.common.audit import log_audit
from agent.routes.voice_blueprint import (
    _VOICE_MAX_FORM_MEMORY_BYTES,
    _VOICE_MAX_FORM_PARTS,
    _VOICE_MULTIPART_OVERHEAD_BYTES,
    _bound_voice_request_body_before_form_parsing,
    _voice_request_too_large,
    voice_bp,
)
from agent.routes.voice_command_routes import (
    command,
    goal,
)
from agent.routes.voice_hub_execution import (
    _complete_deferred_hub_voice_execution,
    _execute_hub_voice_request,
    _fail_deferred_hub_voice_execution,
    _HubVoiceExecution,
    _HubVoiceFailureContext,
    _recover_hub_voice_execution,
)
from agent.routes.voice_recognition_context import (
    _hub_effective_configuration,
    _recognition_context,
    _runtime_voice_configuration,
)
from agent.routes.voice_request_deadlines import (
    _VOICE_STREAM_FINALIZATION_GRACE_SECONDS,
    _assert_stream_preview_context,
    _context_with_remaining_deadline,
    _deadline_epoch_ms,
    _stream_deadline_budget,
    _stream_preview_payload,
    _stream_request_context,
)
from agent.routes.voice_request_policy import (
    _enforce_voice_policy,
    _voice_privacy_state,
)
from agent.routes.voice_request_support import (
    _audit_identity,
    _deadline_seconds,
    _governance_error,
    _mapping,
    _max_audio_mb,
    _observe,
    _principal,
    _provider_error,
    _read_audio_field,
    _response_observation,
    _store_audio_enabled,
    _voice_admission_limits,
    _voice_request_ref,
)
from agent.routes.voice_source_correction_routes import (
    _authorize_semantic_source_consent,
    _authorize_semantic_source_session,
    correct_semantic_speech_source,
)
from agent.routes.voice_stream_create_routes import (
    create_voice_stream,
)
from agent.routes.voice_stream_lifecycle_routes import (
    _fail_finalize_and_cleanup,
    delete_voice_stream,
    finalize_voice_stream,
    get_voice_stream,
    push_voice_stream_chunk,
)
from agent.routes.voice_transcription_routes import (
    capabilities,
    transcribe,
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

__all__ = [
    "_HubVoiceExecution",
    "_HubVoiceFailureContext",
    "_VOICE_MAX_FORM_MEMORY_BYTES",
    "_VOICE_MAX_FORM_PARTS",
    "_VOICE_MULTIPART_OVERHEAD_BYTES",
    "_VOICE_STREAM_FINALIZATION_GRACE_SECONDS",
    "_assert_stream_preview_context",
    "_audit_identity",
    "_authorize_semantic_source_consent",
    "_authorize_semantic_source_session",
    "_bound_voice_request_body_before_form_parsing",
    "_complete_deferred_hub_voice_execution",
    "_context_with_remaining_deadline",
    "_deadline_epoch_ms",
    "_deadline_seconds",
    "_enforce_voice_policy",
    "_execute_hub_voice_request",
    "_fail_deferred_hub_voice_execution",
    "_fail_finalize_and_cleanup",
    "_governance_error",
    "_hub_effective_configuration",
    "_mapping",
    "_max_audio_mb",
    "_observe",
    "_principal",
    "_provider_error",
    "_read_audio_field",
    "_recognition_context",
    "_recover_hub_voice_execution",
    "_response_observation",
    "_runtime_voice_configuration",
    "_store_audio_enabled",
    "_stream_deadline_budget",
    "_stream_preview_payload",
    "_stream_request_context",
    "_voice_admission_limits",
    "_voice_privacy_state",
    "_voice_request_ref",
    "_voice_request_too_large",
    "capabilities",
    "command",
    "correct_semantic_speech_source",
    "create_voice_stream",
    "delete_voice_stream",
    "finalize_voice_stream",
    "generative_corrector_capability_bundle",
    "get_exposure_policy_service",
    "get_share_session_service",
    "get_speech_evidence_consent_service",
    "get_voice_admission_service",
    "get_voice_generative_corrector_service",
    "get_voice_generative_judge_service",
    "get_voice_provider_service",
    "get_voice_restricted_choice_service",
    "get_voice_result_artifact_service",
    "get_voice_stream",
    "goal",
    "log_audit",
    "push_voice_stream_chunk",
    "transcribe",
    "voice_bp",
]
