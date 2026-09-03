"""Mandatory process-local security policy for the optional DSPy runtime."""

from __future__ import annotations

from typing import Any


class DspyRuntimeSecurityPolicy:
    """Disable executable disk-cache deserialization before DSPy is used.

    DSPy 3.2.1 depends on DiskCache 5.6.3, whose default pickle-backed disk
    serialization has no fixed release for CVE-2025-69872.  Ananta therefore
    permits only a bounded, process-local memory cache.  Workers are disposable,
    so cache persistence is not part of the execution contract.
    """

    MEMORY_MAX_ENTRIES = 4096

    @classmethod
    def apply(cls, dspy_module: Any) -> None:
        configure_cache = getattr(dspy_module, "configure_cache", None)
        if not callable(configure_cache):
            raise RuntimeError("dspy_secure_cache_configuration_unavailable")
        configure_cache(
            enable_disk_cache=False,
            enable_memory_cache=True,
            memory_max_entries=cls.MEMORY_MAX_ENTRIES,
        )


__all__ = ["DspyRuntimeSecurityPolicy"]
