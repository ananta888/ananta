from __future__ import annotations

import os
import shutil

from client_surfaces.operator_tui.visual.runtime.view_capability_report import ViewCapabilityReport


def detect_carbonyl_browser(
    *,
    carbonyl_binary: str = "carbonyl",
    config_path: str = "",
) -> ViewCapabilityReport:
    """Detect whether the carbonyl browser binary is available.

    Checks the configured binary path first, then falls back to PATH lookup.
    Never raises — returns an unavailable report on any failure.

    Args:
        carbonyl_binary: Binary name or absolute path to carbonyl.
        config_path: Optional explicit absolute path override from config.

    Returns:
        ViewCapabilityReport with view_id="carbonyl_browser".
    """
    binary = (config_path.strip() or carbonyl_binary.strip() or "carbonyl")

    # Try explicit absolute path first
    if os.path.isabs(binary):
        if os.path.isfile(binary) and os.access(binary, os.X_OK):
            return ViewCapabilityReport(
                view_id="carbonyl_browser",
                display_name="Carbonyl Browser",
                available=True,
                extra={"binary_path": binary, "detection": "absolute_path"},
            )
        return ViewCapabilityReport(
            view_id="carbonyl_browser",
            display_name="Carbonyl Browser",
            available=False,
            unavailable_reason=f"binary not found or not executable: {binary}",
            extra={"binary_path": binary, "detection": "absolute_path"},
        )

    # Search PATH
    resolved = shutil.which(binary)
    if resolved:
        return ViewCapabilityReport(
            view_id="carbonyl_browser",
            display_name="Carbonyl Browser",
            available=True,
            extra={"binary_path": resolved, "detection": "PATH"},
        )

    return ViewCapabilityReport(
        view_id="carbonyl_browser",
        display_name="Carbonyl Browser",
        available=False,
        unavailable_reason=f"'{binary}' not found on PATH — install carbonyl or set carbonyl_binary in config",
        extra={"binary_searched": binary, "detection": "PATH"},
    )
