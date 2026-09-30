"""Compatibility re-export of the browser capability state and its ports."""

from agent.models.sfu_browser_capability import (
    CapabilityState,
    SfuBrowserCapabilitySnapshot,
    SfuBrowserCapabilityWriteResult,
    unknown_capability,
)
from agent.ports.sfu_browser_capability import (
    SfuBrowserCapabilityReadPort,
    SfuBrowserCapabilityRepositoryPort,
)

__all__ = [
    "CapabilityState",
    "SfuBrowserCapabilityReadPort",
    "SfuBrowserCapabilityRepositoryPort",
    "SfuBrowserCapabilitySnapshot",
    "SfuBrowserCapabilityWriteResult",
    "unknown_capability",
]
