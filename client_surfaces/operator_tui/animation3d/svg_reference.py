from __future__ import annotations

from agent.cli.logo_assets import load_logo


def load_fallback_frame(
    width: int,
    color: bool,
    max_lines: int | None = None,
) -> str:
    """Load a static 2D frame derived from ananta.svg.

    Used as a comparison reference and as fallback when the 3D geometry
    model does not apply (non-TTY, too small, disabled).
    """
    return load_logo(width=width, color=color, prefer_ascii=True, max_lines=max_lines)


def reference_line_count(
    width: int,
    color: bool,
    max_lines: int | None = None,
) -> int:
    raw = load_fallback_frame(width, color, max_lines=max_lines)
    if not raw:
        return 0
    return len(raw.split("\n"))
