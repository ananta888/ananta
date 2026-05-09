"""Client surface runtime packages."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class ClientSurface(Protocol):
    def get_type(self) -> str: ...
