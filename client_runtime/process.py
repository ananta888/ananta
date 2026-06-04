from __future__ import annotations

import pty as _pty
import subprocess as _subprocess
from typing import Any

DEVNULL = _subprocess.DEVNULL
PIPE = _subprocess.PIPE
CalledProcessError = _subprocess.CalledProcessError
Popen = _subprocess.Popen
SubprocessError = _subprocess.SubprocessError
TimeoutExpired = _subprocess.TimeoutExpired


def run(*args: Any, **kwargs: Any) -> _subprocess.CompletedProcess[Any]:
    return _subprocess.run(*args, **kwargs)


def openpty() -> tuple[int, int]:
    return _pty.openpty()
