"""Training backend strategies; heavyweight dependencies are lazy imported."""

from worker.training.backends.base import TrainingBackend, TrainingBackendError
from worker.training.backends.mock import MockTrainingBackend
from worker.training.backends.peft_trl import PeftTrlTrainingBackend
from worker.training.backends.unsloth_audio import UnslothAudioTrainingBackend
from worker.training.backends.unsloth_embedding import UnslothEmbeddingTrainingBackend
from worker.training.backends.unsloth import UnslothTrainingBackend
from worker.training.backends.unsloth_vision import UnslothVisionTrainingBackend

__all__ = [
    "MockTrainingBackend",
    "PeftTrlTrainingBackend",
    "TrainingBackend",
    "TrainingBackendError",
    "UnslothAudioTrainingBackend",
    "UnslothEmbeddingTrainingBackend",
    "UnslothTrainingBackend",
    "UnslothVisionTrainingBackend",
]
