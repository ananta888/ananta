"""Isolated worker-side speech adaptation execution package."""

from .backend import SpeechTrainingBackend
from .backend_registry import SpeechTrainingBackendRegistry

__all__ = ["SpeechTrainingBackend", "SpeechTrainingBackendRegistry"]
