"""Tests for CarbonylProfileManager (oidc-003).

Tests:
- Two profiles for same provider get different dirs
- Ephemeral profile is deleted on cleanup
- Named profile is NOT deleted on cleanup
- Profile paths cannot escape profile_root (path traversal prevention)
- get_or_create returns existing profile by ID
- cleanup_all_ephemeral removes all ephemeral profiles
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from client_surfaces.operator_tui.visual.browser.carbonyl_profile_manager import (
    CarbonylProfile,
    CarbonylProfileManager,
)


class TestProfileCreation(unittest.TestCase):
    """Profile creation basics."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name) / "profiles"
        self._mgr = CarbonylProfileManager(profile_root=self._root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_create_profile_returns_carbonyl_profile(self):
        """create_profile must return a CarbonylProfile."""
        p = self._mgr.create_profile(provider_id="keycloak")
        self.assertIsInstance(p, CarbonylProfile)

    def test_create_profile_creates_directory(self):
        """Profile directory must exist after create_profile."""
        p = self._mgr.create_profile(provider_id="keycloak")
        self.assertTrue(p.profile_dir.exists())
        self.assertTrue(p.profile_dir.is_dir())

    def test_two_profiles_same_provider_different_dirs(self):
        """Two calls to create_profile must produce different directories."""
        p1 = self._mgr.create_profile(provider_id="keycloak")
        p2 = self._mgr.create_profile(provider_id="keycloak")
        self.assertNotEqual(p1.profile_dir, p2.profile_dir)
        self.assertNotEqual(p1.profile_id, p2.profile_id)

    def test_profile_dir_under_root(self):
        """Profile directory must be under profile_root."""
        p = self._mgr.create_profile(provider_id="keycloak")
        self.assertTrue(str(p.profile_dir).startswith(str(self._root.resolve())))

    def test_ephemeral_default(self):
        """Profiles are ephemeral by default."""
        p = self._mgr.create_profile(provider_id="keycloak")
        self.assertTrue(p.ephemeral)

    def test_non_ephemeral_profile(self):
        """Named profiles have ephemeral=False."""
        p = self._mgr.create_profile(provider_id="keycloak", ephemeral=False)
        self.assertFalse(p.ephemeral)


class TestEphemeralCleanup(unittest.TestCase):
    """Ephemeral profiles must be deleted on cleanup."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name) / "profiles"
        self._mgr = CarbonylProfileManager(profile_root=self._root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_ephemeral_profile_deleted_on_cleanup(self):
        """cleanup_profile must delete ephemeral profile directory."""
        p = self._mgr.create_profile(provider_id="keycloak", ephemeral=True)
        self.assertTrue(p.profile_dir.exists())
        self._mgr.cleanup_profile(p)
        self.assertFalse(p.profile_dir.exists())

    def test_named_profile_not_deleted_on_cleanup(self):
        """cleanup_profile must NOT delete named (non-ephemeral) profile directory."""
        p = self._mgr.create_profile(provider_id="keycloak", ephemeral=False)
        self.assertTrue(p.profile_dir.exists())
        self._mgr.cleanup_profile(p)
        self.assertTrue(p.profile_dir.exists())

    def test_cleanup_all_ephemeral_removes_all(self):
        """cleanup_all_ephemeral must remove all ephemeral profiles."""
        p1 = self._mgr.create_profile(provider_id="keycloak", ephemeral=True)
        p2 = self._mgr.create_profile(provider_id="keycloak", ephemeral=True)
        p3 = self._mgr.create_profile(provider_id="keycloak", ephemeral=False)

        self._mgr.cleanup_all_ephemeral()

        self.assertFalse(p1.profile_dir.exists())
        self.assertFalse(p2.profile_dir.exists())
        self.assertTrue(p3.profile_dir.exists())  # named, must survive

    def test_double_cleanup_is_safe(self):
        """Calling cleanup_profile twice must not raise."""
        p = self._mgr.create_profile(provider_id="keycloak", ephemeral=True)
        self._mgr.cleanup_profile(p)
        # Second call should not raise
        self._mgr.cleanup_profile(p)


class TestGetOrCreate(unittest.TestCase):
    """get_or_create must return existing profile if ID exists."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name) / "profiles"
        self._mgr = CarbonylProfileManager(profile_root=self._root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_get_or_create_returns_existing(self):
        """get_or_create must return the same profile when given an existing ID."""
        p1 = self._mgr.create_profile(provider_id="keycloak")
        p2 = self._mgr.get_or_create(
            profile_id=p1.profile_id,
            provider_id="keycloak",
        )
        self.assertEqual(p1.profile_id, p2.profile_id)
        self.assertEqual(p1.profile_dir, p2.profile_dir)

    def test_get_or_create_creates_new_if_missing(self):
        """get_or_create must create a new profile when ID is unknown."""
        p = self._mgr.get_or_create(
            profile_id="new-id-abc",
            provider_id="keycloak",
        )
        self.assertIsNotNone(p)
        self.assertTrue(p.profile_dir.exists())


class TestPathTraversalPrevention(unittest.TestCase):
    """Profile paths must not escape profile_root."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name) / "profiles"
        self._mgr = CarbonylProfileManager(profile_root=self._root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_profile_root_returns_resolved_path(self):
        """profile_root() must return a resolved absolute path."""
        root = self._mgr.profile_root()
        self.assertTrue(root.is_absolute())

    def test_traversal_attempt_raises(self):
        """A name with path traversal characters must raise ValueError."""
        # Directly call internal helper with a malicious name
        with self.assertRaises(ValueError):
            self._mgr._safe_profile_dir("../../etc/passwd")

    def test_normal_name_does_not_raise(self):
        """A safe name must not raise."""
        # Should not raise
        path = self._mgr._safe_profile_dir("keycloak-12345")
        self.assertTrue(str(path).startswith(str(self._root.resolve())))


class TestProfileRoot(unittest.TestCase):
    """Default profile root configuration."""

    def test_custom_root_used(self):
        """Custom profile_root must be used."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "my-profiles"
            mgr = CarbonylProfileManager(profile_root=root)
            self.assertEqual(mgr.profile_root(), root.resolve())


if __name__ == "__main__":
    unittest.main()
