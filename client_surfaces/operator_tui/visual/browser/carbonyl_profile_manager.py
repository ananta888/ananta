"""Isolated Carbonyl browser profile manager.

Each OIDC login session or browser session gets a unique, isolated user-data-dir
under the configured profile_root.  Ephemeral profiles are deleted on cleanup.
Named (persisted) profiles require explicit opt-in via ``ephemeral=False``.

Security invariants:
- Profile directories are always under profile_root (path traversal prevented).
- Two sessions always get different directories.
- Ephemeral profiles are deleted when ``cleanup_profile()`` is called.
"""
from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


_DEFAULT_PROFILE_ROOT = Path(".ananta/tmp/carbonyl-profiles")


@dataclass
class CarbonylProfile:
    """An isolated Carbonyl browser profile."""
    profile_id: str
    profile_dir: Path
    ephemeral: bool
    provider_id: str
    created_at: float = field(default_factory=time.time)

    def __repr__(self) -> str:
        return (
            f"CarbonylProfile(profile_id={self.profile_id!r}, "
            f"profile_dir={self.profile_dir!r}, "
            f"ephemeral={self.ephemeral!r}, "
            f"provider_id={self.provider_id!r})"
        )


class CarbonylProfileManager:
    """Creates, tracks and cleans up isolated Carbonyl browser profiles.

    Usage::

        mgr = CarbonylProfileManager()
        profile = mgr.create_profile(provider_id="keycloak_ananta", ephemeral=True)
        # pass profile.profile_dir as --user-data-dir to carbonyl
        mgr.cleanup_profile(profile)

    Two calls to ``create_profile`` always return different directories even
    when given the same ``provider_id``.
    """

    def __init__(self, profile_root: Optional[Path] = None) -> None:
        self._root = (profile_root or _DEFAULT_PROFILE_ROOT).resolve()
        self._profiles: dict[str, CarbonylProfile] = {}

    def profile_root(self) -> Path:
        """Return the resolved profile root directory."""
        return self._root

    def create_profile(
        self,
        provider_id: str,
        ephemeral: bool = True,
    ) -> CarbonylProfile:
        """Create a new, unique browser profile directory.

        Args:
            provider_id: Identifier of the OIDC provider this profile is for.
            ephemeral: If ``True`` (default), the profile directory is deleted
                when ``cleanup_profile()`` is called.

        Returns:
            A ``CarbonylProfile`` with a freshly created directory.
        """
        profile_id = str(uuid.uuid4())
        safe_name = f"{_sanitize(provider_id)}-{profile_id}"
        profile_dir = self._safe_profile_dir(safe_name)
        profile_dir.mkdir(parents=True, exist_ok=False)

        profile = CarbonylProfile(
            profile_id=profile_id,
            profile_dir=profile_dir,
            ephemeral=ephemeral,
            provider_id=provider_id,
        )
        self._profiles[profile_id] = profile
        return profile

    def get_or_create(
        self,
        profile_id: str,
        provider_id: str,
        ephemeral: bool = True,
    ) -> CarbonylProfile:
        """Return an existing profile by ID or create a new one.

        Args:
            profile_id: The profile ID to look up.
            provider_id: Provider ID used if a new profile must be created.
            ephemeral: Ephemeral flag used only when creating a new profile.

        Returns:
            The existing or newly created ``CarbonylProfile``.
        """
        if profile_id in self._profiles:
            return self._profiles[profile_id]
        # Create a profile with a predetermined ID
        safe_name = f"{_sanitize(provider_id)}-{profile_id}"
        profile_dir = self._safe_profile_dir(safe_name)
        profile_dir.mkdir(parents=True, exist_ok=True)

        profile = CarbonylProfile(
            profile_id=profile_id,
            profile_dir=profile_dir,
            ephemeral=ephemeral,
            provider_id=provider_id,
        )
        self._profiles[profile_id] = profile
        return profile

    def cleanup_profile(self, profile: CarbonylProfile) -> None:
        """Delete the profile directory if it is ephemeral.

        No-op for non-ephemeral (named) profiles.

        Args:
            profile: The profile to clean up.
        """
        self._profiles.pop(profile.profile_id, None)
        if profile.ephemeral and profile.profile_dir.exists():
            shutil.rmtree(profile.profile_dir, ignore_errors=True)

    def cleanup_all_ephemeral(self) -> None:
        """Delete all ephemeral profile directories tracked by this manager."""
        for profile_id in list(self._profiles.keys()):
            profile = self._profiles.get(profile_id)
            if profile and profile.ephemeral:
                self.cleanup_profile(profile)

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _safe_profile_dir(self, name: str) -> Path:
        """Resolve and validate a profile directory path.

        Ensures the resolved path stays under profile_root to prevent
        path traversal attacks.

        Args:
            name: A filename-safe profile name.

        Returns:
            Resolved absolute path under profile_root.

        Raises:
            ValueError: If the resolved path escapes profile_root.
        """
        candidate = (self._root / name).resolve()
        # Ensure it stays under root
        try:
            candidate.relative_to(self._root)
        except ValueError:
            raise ValueError(
                f"Profile path {candidate!r} escapes profile_root {self._root!r}"
            )
        return candidate


def _sanitize(value: str) -> str:
    """Return a filesystem-safe version of *value* (alphanumeric + dash)."""
    return "".join(c if c.isalnum() or c == "-" else "_" for c in value)[:48]
