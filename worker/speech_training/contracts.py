"""Worker import facade for the shared speech adaptation wire contract."""

from ananta_contracts.speech_adaptation import (
    CONTRACT_VERSION,
    RESULT_TYPE,
    SUPPORTED_BACKENDS,
    TRAIN_JOB_TYPE,
    SpeechAdaptationContractError,
    SpeechAdaptationJob,
    SpeechAdaptationResult,
    SpeechArtifactDescriptor,
    canonical_json,
    canonical_sha256,
)

__all__ = [
    "CONTRACT_VERSION",
    "RESULT_TYPE",
    "SUPPORTED_BACKENDS",
    "TRAIN_JOB_TYPE",
    "SpeechAdaptationContractError",
    "SpeechAdaptationJob",
    "SpeechAdaptationResult",
    "SpeechArtifactDescriptor",
    "canonical_json",
    "canonical_sha256",
]
