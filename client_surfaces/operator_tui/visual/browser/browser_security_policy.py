from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class BrowserSecurityPolicy:
    """Security policy for the Carbonyl center-browser mode.

    Defaults enforce local-only rendering with no remote network access.
    Remote URLs must be explicitly allowed via allow_remote_urls=True.

    Temp files are created under temp_root and tracked for cleanup.
    """
    allow_remote_urls: bool = False
    allowed_url_schemes: list[str] = field(default_factory=lambda: ["file", "data"])
    temp_root: str = ".ananta/tmp/center-browser"
    cleanup_temp_files: bool = True
    network_policy: str = "disabled_by_default"

    # Internal: track created temp files for cleanup
    _temp_files: list[Path] = field(default_factory=list, repr=False, compare=False)

    def validate_url(self, url: str) -> tuple[bool, str]:
        """Validate whether url is allowed under this policy.

        Returns:
            (allowed: bool, reason: str)
        """
        url = url.strip()
        if not url:
            return False, "empty URL"

        try:
            parsed = urlparse(url)
        except Exception as exc:
            return False, f"invalid URL: {exc}"

        scheme = (parsed.scheme or "").lower()

        # file:// and data: URIs are always allowed regardless of remote policy
        if scheme in {"file", "data", ""}:
            return True, "local scheme allowed"

        if self.allow_remote_urls:
            allowed_schemes = {s.lower() for s in self.allowed_url_schemes}
            if scheme in allowed_schemes or "*" in allowed_schemes:
                return True, "remote URL explicitly allowed"
            return False, (
                f"scheme '{scheme}' not in allowed_url_schemes={self.allowed_url_schemes}"
            )

        # Remote disabled by default
        if scheme in {"http", "https", "ftp", "ftps"}:
            return False, (
                "remote URLs disabled by default (network_policy=disabled_by_default). "
                "Set allow_remote_urls=true in operator_tui.center_browser config to enable."
            )

        # Other unknown schemes
        return False, f"unknown URL scheme '{scheme}' — not permitted"

    def create_temp_file(self, content: str, suffix: str = ".html") -> Path:
        """Write content to a tracked temp file under temp_root.

        Creates temp_root if it does not exist.
        The file is registered for cleanup() to remove.

        Args:
            content: Text content to write.
            suffix:  File suffix (default: '.html').

        Returns:
            Path to the created file.
        """
        root = Path(self.temp_root)
        root.mkdir(parents=True, exist_ok=True)
        fd, path_str = tempfile.mkstemp(suffix=suffix, dir=str(root))
        path = Path(path_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        self._temp_files.append(path)
        return path

    def cleanup(self) -> None:
        """Remove all tracked temp files if cleanup_temp_files is True."""
        if not self.cleanup_temp_files:
            return
        remaining: list[Path] = []
        for path in self._temp_files:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                remaining.append(path)
        self._temp_files = remaining
