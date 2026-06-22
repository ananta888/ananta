"""HCCA-016: Controlled retrieval tool for accessing original content from the CCR Store.

Provides an auditable, type-checked interface to the Compressed Content
Reference (CCR) store so agents can safely retrieve original content that
was replaced by a compression placeholder.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.services.context_compression import CCRStore


@dataclass(frozen=True)
class CCRRetrievalRequest:
    ref: str
    requester_id: str = ""  # who is requesting (for audit)
    allowed_content_types: list[str] = field(default_factory=list)  # empty = allow all


@dataclass(frozen=True)
class CCRRetrievalResult:
    ref: str
    found: bool
    content: str | None
    content_type: str
    decision: str  # "found" | "not_found" | "expired" | "type_not_allowed" | "store_disabled"
    reason: str
    byte_size: int
    elapsed_ms: float


class CCRRetrievalTool:
    def __init__(self, ccr_store: CCRStore | None) -> None:
        self._store = ccr_store

    def retrieve(self, request: CCRRetrievalRequest) -> CCRRetrievalResult:
        """Retrieve original content from the CCR store.

        Steps:
        1. Check if the store is configured.
        2. Check allowed_content_types (if non-empty, verify content_type matches).
        3. Call ccr_store.retrieve(ref).
        4. Return appropriate result.
        """
        t0 = time.monotonic()

        if self._store is None:
            elapsed = (time.monotonic() - t0) * 1000.0
            return CCRRetrievalResult(
                ref=request.ref,
                found=False,
                content=None,
                content_type="",
                decision="store_disabled",
                reason="CCR store is not configured",
                byte_size=0,
                elapsed_ms=elapsed,
            )

        try:
            entry = self._store.retrieve(request.ref)
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000.0
            return CCRRetrievalResult(
                ref=request.ref,
                found=False,
                content=None,
                content_type="",
                decision="not_found",
                reason=f"store error: {exc}",
                byte_size=0,
                elapsed_ms=elapsed,
            )

        if entry is None:
            elapsed = (time.monotonic() - t0) * 1000.0
            return CCRRetrievalResult(
                ref=request.ref,
                found=False,
                content=None,
                content_type="",
                decision="not_found",
                reason="ref not found in CCR store",
                byte_size=0,
                elapsed_ms=elapsed,
            )

        # Check for expiry (CCREntry may expose an is_expired property/method).
        is_expired = getattr(entry, "is_expired", None)
        if callable(is_expired) and is_expired():
            elapsed = (time.monotonic() - t0) * 1000.0
            return CCRRetrievalResult(
                ref=request.ref,
                found=False,
                content=None,
                content_type=getattr(entry, "content_type", ""),
                decision="expired",
                reason="CCR entry has expired",
                byte_size=0,
                elapsed_ms=elapsed,
            )

        content_type = getattr(entry, "content_type", "")
        if request.allowed_content_types and content_type not in request.allowed_content_types:
            elapsed = (time.monotonic() - t0) * 1000.0
            return CCRRetrievalResult(
                ref=request.ref,
                found=False,
                content=None,
                content_type=content_type,
                decision="type_not_allowed",
                reason=(
                    f"content_type {content_type!r} not in allowed list "
                    f"{request.allowed_content_types!r}"
                ),
                byte_size=0,
                elapsed_ms=elapsed,
            )

        content = getattr(entry, "content", None) or ""
        elapsed = (time.monotonic() - t0) * 1000.0
        return CCRRetrievalResult(
            ref=request.ref,
            found=True,
            content=content,
            content_type=content_type,
            decision="found",
            reason="ok",
            byte_size=len(content.encode("utf-8")) if isinstance(content, str) else 0,
            elapsed_ms=elapsed,
        )

    def describe(self) -> dict[str, Any]:
        """Return diagnostic info about this tool."""
        enabled = self._store is not None
        store_path = ""
        ttl_hours = None
        diagnostics: dict[str, Any] = {}
        if self._store is not None:
            store_path = str(getattr(self._store, "store_path", "") or "")
            ttl_hours = getattr(self._store, "ttl_hours", None)
            if hasattr(self._store, "diagnostics"):
                try:
                    diagnostics = dict(self._store.diagnostics() or {})
                except Exception:
                    pass
        return {
            "enabled": enabled,
            "store_path": store_path,
            "ttl_hours": ttl_hours,
            "diagnostics": diagnostics,
        }

    @classmethod
    def disabled(cls) -> CCRRetrievalTool:
        """Return a no-op tool that always returns store_disabled."""
        return cls(ccr_store=None)
