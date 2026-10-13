"""Narrow process-launch context port; security policy belongs to composition."""

from contextlib import nullcontext
from pathlib import Path
from typing import ContextManager, Mapping, Protocol, Sequence

PreparedLaunch = tuple[Sequence[str], Mapping[str, str], tuple[int, ...]]


class ProcessLaunchPort(Protocol):
    def __call__(self, argv: Sequence[str], *, workspace: Path,
                 environment: Mapping[str, str]) -> ContextManager[PreparedLaunch]: ...


def direct_process_launch(argv, *, workspace, environment):
    del workspace
    return nullcontext((argv, environment, ()))
