"""Compatibility facade for the shared model-recovery transport contract.

Production code should import this container-neutral contract from
``ananta_contracts.model_recovery``.  This module remains available for
backward compatibility with existing integrations.
"""

from ananta_contracts.model_recovery import (
    MODEL_RECOVERY_SIGNAL_SCHEMA as MODEL_RECOVERY_SIGNAL_SCHEMA,
    NON_RECOVERABLE_TERMINAL_REASONS as NON_RECOVERABLE_TERMINAL_REASONS,
    RECOVERABLE_TERMINAL_REASONS as RECOVERABLE_TERMINAL_REASONS,
    __all__ as __all__,
    aggregate_model_recovery_signals as aggregate_model_recovery_signals,
    build_model_recovery_signal as build_model_recovery_signal,
    is_recoverable_model_error_type as is_recoverable_model_error_type,
    metadata_from_llm_error as metadata_from_llm_error,
    normalize_model_recovery_error_type as normalize_model_recovery_error_type,
    sanitize_terminal_model_recovery_signal as sanitize_terminal_model_recovery_signal,
)
