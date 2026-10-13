"""Parent-owned Task scratch survives child termination and is retired by its owner."""

from pathlib import Path
from tempfile import TemporaryDirectory


class TaskExecutionScratch:
    def __init__(self, *, parent="/tmp"):
        self._directory = TemporaryDirectory(prefix="ananta-meet-cuda-", dir=parent)
        try:
            self._temporary = Path(self._directory.name) / "scratch"
            self._temporary.mkdir(mode=0o700)
        except BaseException:
            self._directory.cleanup()
            raise

    @property
    def environment(self):
        return {"CUDA_CACHE_PATH": self._directory.name, "CUDA_CACHE_MAXSIZE": "33554432",
                "TMPDIR": str(self._temporary)}

    def close(self):
        self._directory.cleanup()
