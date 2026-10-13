"""Private compiled-kernel scratch owned by one assigned execution lifetime."""

from worker.meet_media.companion_process_transport import CompanionExecutionError
from worker.meet_media.companion_task_scratch import TaskExecutionScratch as TaskCudaCache

__all__ = ["CachedCompanionExecution", "TaskCudaCache"]


class CachedCompanionExecution:
    """Retain kernel scratch across utterance processes, retire it with its Task."""

    def __init__(self, execution, cache):
        self._execution, self._cache = execution, cache
        self._closed = False

    def call(self, kind, text, *, cooperate):
        if self._closed:
            raise CompanionExecutionError("meet_companion_execution_closed")
        return self._execution.call(kind, text, cooperate=cooperate)

    def close(self):
        self._closed = True
        try:
            self._execution.close()
        finally:
            self._cache.close()
