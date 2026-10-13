"""Compatibility names for the shared Native result authority port."""

from agent.ports.native_result_authority import (
    NativeResultAuthority as PiResultAuthority,
)
from agent.ports.native_result_authority import (
    NativeResultAuthorityPort as PiResultAuthorityPort,
)

__all__ = ["PiResultAuthority", "PiResultAuthorityPort"]
