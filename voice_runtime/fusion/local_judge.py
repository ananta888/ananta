"""Backward-compatible runtime import for the shared judge contracts."""

from ananta_contracts.voice_judge import (
    STRICT_CHOICE_OPERATIONS,
    CandidateOnlyResultValidator,
    GenerativeJudgeOutcome,
    GenerativeJudgeRequest,
    GenerativeResultValidator,
    LocalGenerativeJudge,
    LocalGenerativeJudgePolicy,
    LocalJudgeTransport,
    RestrictedChoiceExecutor,
    StrictChoice,
    StrictChoiceJudge,
    StrictChoiceOutcome,
    StrictChoiceRequest,
    validate_loopback_endpoint,
)

__all__ = [
    "STRICT_CHOICE_OPERATIONS",
    "CandidateOnlyResultValidator",
    "GenerativeJudgeOutcome",
    "GenerativeJudgeRequest",
    "GenerativeResultValidator",
    "LocalGenerativeJudge",
    "LocalGenerativeJudgePolicy",
    "LocalJudgeTransport",
    "RestrictedChoiceExecutor",
    "StrictChoice",
    "StrictChoiceJudge",
    "StrictChoiceOutcome",
    "StrictChoiceRequest",
    "validate_loopback_endpoint",
]
