from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Supported content_type values
CONTENT_TYPES = frozenset({
    "markdown",
    "mermaid_markdown",
    "plain_text",
    "ansi_text",
    "source_code",
    "html_preview",
    "artifact_preview",
})


@dataclass
class CenterContentSnapshot:
    """Serializable snapshot of current CenterViewport content for browser-mode rendering.

    All fields are read-only after construction — snapshotting must not mutate the
    active VisualView state.
    """
    content_type: str  # one of CONTENT_TYPES
    title: str
    source_text: str
    html_text: str  # pre-rendered HTML if available, else ""
    metadata: dict[str, Any] = field(default_factory=dict)
    scroll_position: int = 0
    unsupported_reason: str = ""  # non-empty if this view cannot be snapshotted

    def is_supported(self) -> bool:
        return not self.unsupported_reason

    def is_html_ready(self) -> bool:
        return bool(self.html_text)


def unsupported_snapshot(*, reason: str, title: str = "") -> CenterContentSnapshot:
    """Convenience constructor for views that cannot produce a snapshot."""
    return CenterContentSnapshot(
        content_type="plain_text",
        title=title or "Unsupported",
        source_text="",
        html_text="",
        unsupported_reason=reason,
    )


class BrowserSnapshotMixin:
    """Mixin that VisualView classes can include to expose get_browser_snapshot().

    Default implementation returns an unsupported snapshot.  Subclasses should
    override get_browser_snapshot() to return meaningful content.
    """

    def get_browser_snapshot(self) -> CenterContentSnapshot:  # type: ignore[return]
        """Return a CenterContentSnapshot of the current view state.

        Subclasses must override this method.  The default returns an
        unsupported snapshot so that views that have not yet added browser
        support degrade gracefully rather than crashing.
        """
        view_id = getattr(self, "view_id", type(self).__name__)
        return unsupported_snapshot(
            reason=f"view '{view_id}' does not support browser snapshot yet",
            title=str(view_id),
        )
