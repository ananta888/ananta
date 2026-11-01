"""UI guide freshness for the snake config tool loop (CLN-049).

Moved from agent/routes/snakes_ananta_config_tool_loop.py so routes do not
shell out; the route keeps its thin command handler and calls ensure_ui_guide.
"""

from __future__ import annotations

import logging
import pathlib as _pl
import time

logger = logging.getLogger(__name__)

REPO_ROOT = _pl.Path(__file__).parent.parent.parent

UI_GUIDE_PATH = REPO_ROOT / "docs" / "ananta-ui-guide.md"

UI_GUIDE_SCRIPT = REPO_ROOT / "scripts" / "generate_ui_guide.py"

GUIDE_MAX_AGE_SECONDS = 86_400  # regenerate guide if older than 24 h


def ensure_ui_guide(force: bool = False) -> str:
    """Return the UI guide markdown, generating it when missing or stale."""
    needs_regen = force
    if not needs_regen:
        if not UI_GUIDE_PATH.exists():
            needs_regen = True
        else:
            age = time.time() - UI_GUIDE_PATH.stat().st_mtime
            if age > GUIDE_MAX_AGE_SECONDS:
                needs_regen = True

    if needs_regen:
        try:
            import subprocess
            import sys

            subprocess.run(
                [sys.executable, str(UI_GUIDE_SCRIPT)],
                check=False,
                timeout=30,
                cwd=str(REPO_ROOT),
            )
            logger.info("UI guide regenerated: %s", UI_GUIDE_PATH)
        except Exception as exc:
            logger.warning("UI guide generation failed: %s", exc)

    try:
        return UI_GUIDE_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""


__all__ = ["REPO_ROOT", "UI_GUIDE_PATH", "UI_GUIDE_SCRIPT", "GUIDE_MAX_AGE_SECONDS", "ensure_ui_guide"]
